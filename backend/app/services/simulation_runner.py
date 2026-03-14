"""
Исполнитель симуляций OASIS.

Запускает симуляции в фоне, читает логи действий агентов и отдает состояние
в реальном времени.
"""

import os
import sys
import json
import time
import asyncio
import threading
import subprocess
import signal
import atexit
from typing import Dict, Any, List, Optional, Union
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from queue import Queue

from ..config import Config
from ..utils.logger import get_logger
from .zep_graph_memory_updater import ZepGraphMemoryManager
from .simulation_ipc import SimulationIPCClient, CommandType, IPCResponse

logger = get_logger('mirofish.simulation_runner')

# Признак регистрации обработчика очистки
_cleanup_registered = False

# Определение платформы
IS_WINDOWS = sys.platform == 'win32'


class RunnerStatus(str, Enum):
    """Статус раннера."""
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class AgentAction:
    """Запись о действии агента."""
    round_num: int
    timestamp: str
    platform: str  # twitter / reddit
    agent_id: int
    agent_name: str
    action_type: str  # CREATE_POST, LIKE_POST, etc.
    action_args: Dict[str, Any] = field(default_factory=dict)
    result: Optional[str] = None
    success: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "round_num": self.round_num,
            "timestamp": self.timestamp,
            "platform": self.platform,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "action_type": self.action_type,
            "action_args": self.action_args,
            "result": self.result,
            "success": self.success,
        }


@dataclass
class RoundSummary:
    """Сводка по раунду."""
    round_num: int
    start_time: str
    end_time: Optional[str] = None
    simulated_hour: int = 0
    twitter_actions: int = 0
    reddit_actions: int = 0
    active_agents: List[int] = field(default_factory=list)
    actions: List[AgentAction] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "round_num": self.round_num,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "simulated_hour": self.simulated_hour,
            "twitter_actions": self.twitter_actions,
            "reddit_actions": self.reddit_actions,
            "active_agents": self.active_agents,
            "actions_count": len(self.actions),
            "actions": [a.to_dict() for a in self.actions],
        }


@dataclass
class SimulationRunState:
    """Текущее состояние выполнения симуляции."""
    simulation_id: str
    runner_status: RunnerStatus = RunnerStatus.IDLE
    
    # Прогресс
    current_round: int = 0
    total_rounds: int = 0
    simulated_hours: int = 0
    total_simulation_hours: int = 0
    
    # Независимые счетчики по платформам
    twitter_current_round: int = 0
    reddit_current_round: int = 0
    twitter_simulated_hours: int = 0
    reddit_simulated_hours: int = 0
    
    # Состояние платформ
    twitter_running: bool = False
    reddit_running: bool = False
    twitter_actions_count: int = 0
    reddit_actions_count: int = 0
    
    # Признаки завершения платформ
    twitter_completed: bool = False
    reddit_completed: bool = False
    
    # Сводки по раундам
    rounds: List[RoundSummary] = field(default_factory=list)
    
    # Последние действия для UI
    recent_actions: List[AgentAction] = field(default_factory=list)
    max_recent_actions: int = 50
    
    # Метки времени
    started_at: Optional[str] = None
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None
    
    # Ошибка
    error: Optional[str] = None
    
    # PID процесса
    process_pid: Optional[int] = None
    
    def add_action(self, action: AgentAction):
        """Добавляет действие в список последних."""
        self.recent_actions.insert(0, action)
        if len(self.recent_actions) > self.max_recent_actions:
            self.recent_actions = self.recent_actions[:self.max_recent_actions]
        
        if action.platform == "twitter":
            self.twitter_actions_count += 1
        else:
            self.reddit_actions_count += 1
        
        self.updated_at = datetime.now().isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "simulation_id": self.simulation_id,
            "runner_status": self.runner_status.value,
            "current_round": self.current_round,
            "total_rounds": self.total_rounds,
            "simulated_hours": self.simulated_hours,
            "total_simulation_hours": self.total_simulation_hours,
            "progress_percent": round(self.current_round / max(self.total_rounds, 1) * 100, 1),
            # Независимые счетчики по платформам
            "twitter_current_round": self.twitter_current_round,
            "reddit_current_round": self.reddit_current_round,
            "twitter_simulated_hours": self.twitter_simulated_hours,
            "reddit_simulated_hours": self.reddit_simulated_hours,
            "twitter_running": self.twitter_running,
            "reddit_running": self.reddit_running,
            "twitter_completed": self.twitter_completed,
            "reddit_completed": self.reddit_completed,
            "twitter_actions_count": self.twitter_actions_count,
            "reddit_actions_count": self.reddit_actions_count,
            "total_actions_count": self.twitter_actions_count + self.reddit_actions_count,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "process_pid": self.process_pid,
        }
    
    def to_detail_dict(self) -> Dict[str, Any]:
        """Возвращает подробное состояние с последними действиями."""
        result = self.to_dict()
        result["recent_actions"] = [a.to_dict() for a in self.recent_actions]
        result["rounds_count"] = len(self.rounds)
        return result


class SimulationRunner:
    """
    Управляет запуском и сопровождением симуляций.

    Отвечает за:
    1. запуск OASIS в отдельном процессе;
    2. разбор логов действий агентов;
    3. выдачу статуса в реальном времени;
    4. остановку и очистку среды.
    """
    
    # Каталог хранения run-state
    RUN_STATE_DIR = os.path.join(
        os.path.dirname(__file__),
        '../../uploads/simulations'
    )
    
    # Каталог скриптов
    SCRIPTS_DIR = os.path.join(
        os.path.dirname(__file__),
        '../../scripts'
    )
    
    # Состояние в памяти
    _run_states: Dict[str, SimulationRunState] = {}
    _processes: Dict[str, subprocess.Popen] = {}
    _action_queues: Dict[str, Queue] = {}
    _monitor_threads: Dict[str, threading.Thread] = {}
    _stdout_files: Dict[str, Any] = {}
    _stderr_files: Dict[str, Any] = {}
    
    # Флаг обновления памяти графа
    _graph_memory_enabled: Dict[str, bool] = {}
    
    @classmethod
    def get_run_state(cls, simulation_id: str) -> Optional[SimulationRunState]:
        """Возвращает состояние выполнения."""
        if simulation_id in cls._run_states:
            return cls._run_states[simulation_id]
        
        # Пробуем загрузить состояние из файла
        state = cls._load_run_state(simulation_id)
        if state:
            cls._run_states[simulation_id] = state
        return state
    
    @classmethod
    def _load_run_state(cls, simulation_id: str) -> Optional[SimulationRunState]:
        """Загружает состояние выполнения из файла."""
        state_file = os.path.join(cls.RUN_STATE_DIR, simulation_id, "run_state.json")
        if not os.path.exists(state_file):
            return None
        
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            state = SimulationRunState(
                simulation_id=simulation_id,
                runner_status=RunnerStatus(data.get("runner_status", "idle")),
                current_round=data.get("current_round", 0),
                total_rounds=data.get("total_rounds", 0),
                simulated_hours=data.get("simulated_hours", 0),
                total_simulation_hours=data.get("total_simulation_hours", 0),
                # 各平台独立轮次和时间
                twitter_current_round=data.get("twitter_current_round", 0),
                reddit_current_round=data.get("reddit_current_round", 0),
                twitter_simulated_hours=data.get("twitter_simulated_hours", 0),
                reddit_simulated_hours=data.get("reddit_simulated_hours", 0),
                twitter_running=data.get("twitter_running", False),
                reddit_running=data.get("reddit_running", False),
                twitter_completed=data.get("twitter_completed", False),
                reddit_completed=data.get("reddit_completed", False),
                twitter_actions_count=data.get("twitter_actions_count", 0),
                reddit_actions_count=data.get("reddit_actions_count", 0),
                started_at=data.get("started_at"),
                updated_at=data.get("updated_at", datetime.now().isoformat()),
                completed_at=data.get("completed_at"),
                error=data.get("error"),
                process_pid=data.get("process_pid"),
            )
            
            # Загружаем последние действия
            actions_data = data.get("recent_actions", [])
            for a in actions_data:
                state.recent_actions.append(AgentAction(
                    round_num=a.get("round_num", 0),
                    timestamp=a.get("timestamp", ""),
                    platform=a.get("platform", ""),
                    agent_id=a.get("agent_id", 0),
                    agent_name=a.get("agent_name", ""),
                    action_type=a.get("action_type", ""),
                    action_args=a.get("action_args", {}),
                    result=a.get("result"),
                    success=a.get("success", True),
                ))
            
            return state
        except Exception as e:
            logger.error(f"Не удалось загрузить run-state: {str(e)}")
            return None
    
    @classmethod
    def _save_run_state(cls, state: SimulationRunState):
        """Сохраняет run-state в файл."""
        sim_dir = os.path.join(cls.RUN_STATE_DIR, state.simulation_id)
        os.makedirs(sim_dir, exist_ok=True)
        state_file = os.path.join(sim_dir, "run_state.json")
        
        data = state.to_detail_dict()
        
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        cls._run_states[state.simulation_id] = state
    
    @classmethod
    def start_simulation(
        cls,
        simulation_id: str,
        platform: str = "parallel",
        max_rounds: int = None,
        enable_graph_memory_update: bool = False,
        graph_id: str = None
    ) -> SimulationRunState:
        """
        Запускает симуляцию.

        Args:
            simulation_id: ID симуляции
            platform: `twitter`, `reddit` или `parallel`
            max_rounds: ограничение числа раундов
            enable_graph_memory_update: обновлять ли память графа Zep
            graph_id: ID графа Zep

        Returns:
            SimulationRunState
        """
        # Проверяем, не запущена ли симуляция уже сейчас
        existing = cls.get_run_state(simulation_id)
        if existing and existing.runner_status in [RunnerStatus.RUNNING, RunnerStatus.STARTING]:
            raise ValueError(f"Симуляция уже запущена: {simulation_id}")
        
        # Загружаем конфигурацию симуляции
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        
        if not os.path.exists(config_path):
            raise ValueError("Конфигурация симуляции не найдена. Сначала вызови /prepare")
        
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        # Инициализируем состояние выполнения
        time_config = config.get("time_config", {})
        total_hours = time_config.get("total_simulation_hours", 72)
        minutes_per_round = time_config.get("minutes_per_round", 30)
        total_rounds = int(total_hours * 60 / minutes_per_round)
        
        # Если задан max_rounds, обрезаем длительность
        if max_rounds is not None and max_rounds > 0:
            original_rounds = total_rounds
            total_rounds = min(total_rounds, max_rounds)
            if total_rounds < original_rounds:
                logger.info(f"Число раундов сокращено: {original_rounds} -> {total_rounds} (max_rounds={max_rounds})")
        
        state = SimulationRunState(
            simulation_id=simulation_id,
            runner_status=RunnerStatus.STARTING,
            total_rounds=total_rounds,
            total_simulation_hours=total_hours,
            started_at=datetime.now().isoformat(),
        )
        
        cls._save_run_state(state)
        
        # Если включено обновление памяти графа, поднимаем updater
        if enable_graph_memory_update:
            if not graph_id:
                raise ValueError("Для обновления памяти графа нужен graph_id")
            
            try:
                ZepGraphMemoryManager.create_updater(simulation_id, graph_id)
                cls._graph_memory_enabled[simulation_id] = True
                logger.info(f"Обновление памяти графа включено: simulation_id={simulation_id}, graph_id={graph_id}")
            except Exception as e:
                logger.error(f"Не удалось создать updater памяти графа: {e}")
                cls._graph_memory_enabled[simulation_id] = False
        else:
            cls._graph_memory_enabled[simulation_id] = False
        
        # Выбираем скрипт запуска
        if platform == "twitter":
            script_name = "run_twitter_simulation.py"
            state.twitter_running = True
        elif platform == "reddit":
            script_name = "run_reddit_simulation.py"
            state.reddit_running = True
        else:
            script_name = "run_parallel_simulation.py"
            state.twitter_running = True
            state.reddit_running = True
        
        script_path = os.path.join(cls.SCRIPTS_DIR, script_name)
        
        if not os.path.exists(script_path):
            raise ValueError(f"Скрипт не найден: {script_path}")
        
        # Создаем очередь действий
        action_queue = Queue()
        cls._action_queues[simulation_id] = action_queue
        
        # Запускаем процесс симуляции
        try:
            # Собираем команду запуска и используем полные пути
            
            cmd = [
                sys.executable,
                "-u",
                script_path,
                "--config", config_path,
            ]
            
            # Если задан лимит раундов, передаем его в CLI
            if max_rounds is not None and max_rounds > 0:
                cmd.extend(["--max-rounds", str(max_rounds)])
            
            # Пишем stdout/stderr в файл, чтобы процесс не блокировался на буферах
            main_log_path = os.path.join(sim_dir, "simulation.log")
            main_log_file = open(main_log_path, 'w', encoding='utf-8')
            
            # Принудительно включаем UTF-8 в дочернем процессе
            env = os.environ.copy()
            env['PYTHONUTF8'] = '1'
            env['PYTHONIOENCODING'] = 'utf-8'
            env['PYTHONUNBUFFERED'] = '1'
            
            # Рабочая директория - каталог симуляции; создаем новую группу процессов
            process = subprocess.Popen(
                cmd,
                cwd=sim_dir,
                stdout=main_log_file,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                bufsize=1,
                env=env,
                start_new_session=True,
            )
            
            # Сохраняем файловые дескрипторы, чтобы потом закрыть
            cls._stdout_files[simulation_id] = main_log_file
            cls._stderr_files[simulation_id] = None
            
            state.process_pid = process.pid
            state.runner_status = RunnerStatus.RUNNING
            cls._processes[simulation_id] = process
            cls._save_run_state(state)
            
            # Запускаем поток мониторинга
            monitor_thread = threading.Thread(
                target=cls._monitor_simulation,
                args=(simulation_id,),
                daemon=True
            )
            monitor_thread.start()
            cls._monitor_threads[simulation_id] = monitor_thread
            
            logger.info(f"Симуляция запущена: {simulation_id}, pid={process.pid}, platform={platform}")
            
        except Exception as e:
            state.runner_status = RunnerStatus.FAILED
            state.error = str(e)
            cls._save_run_state(state)
            raise
        
        return state
    
    @classmethod
    def _monitor_simulation(cls, simulation_id: str):
        """Следит за процессом симуляции и читает логи действий."""
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        
        # Раздельные логи действий по платформам
        twitter_actions_log = os.path.join(sim_dir, "twitter", "actions.jsonl")
        reddit_actions_log = os.path.join(sim_dir, "reddit", "actions.jsonl")
        
        process = cls._processes.get(simulation_id)
        state = cls.get_run_state(simulation_id)
        
        if not process or not state:
            return
        
        twitter_position = 0
        reddit_position = 0
        
        try:
            while process.poll() is None:
                # Читаем лог Twitter
                if os.path.exists(twitter_actions_log):
                    twitter_position = cls._read_action_log(
                        twitter_actions_log, twitter_position, state, "twitter"
                    )
                
                # Читаем лог Reddit
                if os.path.exists(reddit_actions_log):
                    reddit_position = cls._read_action_log(
                        reddit_actions_log, reddit_position, state, "reddit"
                    )
                
                # Сохраняем статус
                cls._save_run_state(state)
                time.sleep(2)
            
            # После завершения процесса дочитываем логи
            if os.path.exists(twitter_actions_log):
                cls._read_action_log(twitter_actions_log, twitter_position, state, "twitter")
            if os.path.exists(reddit_actions_log):
                cls._read_action_log(reddit_actions_log, reddit_position, state, "reddit")
            
            # Процесс завершился
            exit_code = process.returncode
            
            if exit_code == 0:
                state.runner_status = RunnerStatus.COMPLETED
                state.completed_at = datetime.now().isoformat()
                logger.info(f"Симуляция завершена: {simulation_id}")
            else:
                state.runner_status = RunnerStatus.FAILED
                # Считываем хвост main log с ошибкой
                main_log_path = os.path.join(sim_dir, "simulation.log")
                error_info = ""
                try:
                    if os.path.exists(main_log_path):
                        with open(main_log_path, 'r', encoding='utf-8') as f:
                            error_info = f.read()[-2000:]
                except Exception:
                    pass
                state.error = f"Код завершения процесса: {exit_code}, ошибка: {error_info}"
                logger.error(f"Симуляция завершилась ошибкой: {simulation_id}, error={state.error}")
            
            state.twitter_running = False
            state.reddit_running = False
            cls._save_run_state(state)
            
        except Exception as e:
            logger.error(f"Ошибка в потоке мониторинга: {simulation_id}, error={str(e)}")
            state.runner_status = RunnerStatus.FAILED
            state.error = str(e)
            cls._save_run_state(state)
        
        finally:
            # Останавливаем updater памяти графа
            if cls._graph_memory_enabled.get(simulation_id, False):
                try:
                    ZepGraphMemoryManager.stop_updater(simulation_id)
                    logger.info(f"Обновление памяти графа остановлено: simulation_id={simulation_id}")
                except Exception as e:
                    logger.error(f"Не удалось остановить updater памяти графа: {e}")
                cls._graph_memory_enabled.pop(simulation_id, None)
            
            # Чистим процессные ресурсы
            cls._processes.pop(simulation_id, None)
            cls._action_queues.pop(simulation_id, None)
            
            # Закрываем файловые дескрипторы логов
            if simulation_id in cls._stdout_files:
                try:
                    cls._stdout_files[simulation_id].close()
                except Exception:
                    pass
                cls._stdout_files.pop(simulation_id, None)
            if simulation_id in cls._stderr_files and cls._stderr_files[simulation_id]:
                try:
                    cls._stderr_files[simulation_id].close()
                except Exception:
                    pass
                cls._stderr_files.pop(simulation_id, None)
    
    @classmethod
    def _read_action_log(
        cls, 
        log_path: str, 
        position: int, 
        state: SimulationRunState,
        platform: str
    ) -> int:
        """
        Читает лог действий и обновляет состояние.
        """
        # Проверяем, включено ли обновление памяти графа
        graph_memory_enabled = cls._graph_memory_enabled.get(state.simulation_id, False)
        graph_updater = None
        if graph_memory_enabled:
            graph_updater = ZepGraphMemoryManager.get_updater(state.simulation_id)
        
        try:
            with open(log_path, 'r', encoding='utf-8') as f:
                f.seek(position)
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            action_data = json.loads(line)
                            
                            # Обрабатываем системные события
                            if "event_type" in action_data:
                                event_type = action_data.get("event_type")
                                
                                # Событие завершения симуляции на платформе
                                if event_type == "simulation_end":
                                    if platform == "twitter":
                                        state.twitter_completed = True
                                        state.twitter_running = False
                                        logger.info(f"Twitter завершен: {state.simulation_id}, total_rounds={action_data.get('total_rounds')}, total_actions={action_data.get('total_actions')}")
                                    elif platform == "reddit":
                                        state.reddit_completed = True
                                        state.reddit_running = False
                                        logger.info(f"Reddit завершен: {state.simulation_id}, total_rounds={action_data.get('total_rounds')}, total_actions={action_data.get('total_actions')}")
                                    
                                    # Если все активные платформы завершились, отмечаем всю симуляцию завершенной
                                    all_completed = cls._check_all_platforms_completed(state)
                                    if all_completed:
                                        state.runner_status = RunnerStatus.COMPLETED
                                        state.completed_at = datetime.now().isoformat()
                                        logger.info(f"Все платформы завершены: {state.simulation_id}")
                                
                                # Обновляем информацию о раундах
                                elif event_type == "round_end":
                                    round_num = action_data.get("round", 0)
                                    simulated_hours = action_data.get("simulated_hours", 0)
                                    
                                    # Обновляем счетчики каждой платформы
                                    if platform == "twitter":
                                        if round_num > state.twitter_current_round:
                                            state.twitter_current_round = round_num
                                        state.twitter_simulated_hours = simulated_hours
                                    elif platform == "reddit":
                                        if round_num > state.reddit_current_round:
                                            state.reddit_current_round = round_num
                                        state.reddit_simulated_hours = simulated_hours
                                    
                                    # Общий номер раунда - максимум между платформами
                                    if round_num > state.current_round:
                                        state.current_round = round_num
                                    # Аналогично для смоделированных часов
                                    state.simulated_hours = max(state.twitter_simulated_hours, state.reddit_simulated_hours)
                                
                                continue
                            
                            action = AgentAction(
                                round_num=action_data.get("round", 0),
                                timestamp=action_data.get("timestamp", datetime.now().isoformat()),
                                platform=platform,
                                agent_id=action_data.get("agent_id", 0),
                                agent_name=action_data.get("agent_name", ""),
                                action_type=action_data.get("action_type", ""),
                                action_args=action_data.get("action_args", {}),
                                result=action_data.get("result"),
                                success=action_data.get("success", True),
                            )
                            state.add_action(action)
                            
                            # Обновляем текущий раунд
                            if action.round_num and action.round_num > state.current_round:
                                state.current_round = action.round_num
                            
                            # Отправляем действия в память графа, если это включено
                            if graph_updater:
                                graph_updater.add_activity_from_dict(action_data, platform)
                            
                        except json.JSONDecodeError:
                            pass
                return f.tell()
        except Exception as e:
            logger.warning(f"Не удалось прочитать лог действий: {log_path}, error={e}")
            return position
    
    @classmethod
    def _check_all_platforms_completed(cls, state: SimulationRunState) -> bool:
        """Проверяет, завершились ли все задействованные платформы."""
        sim_dir = os.path.join(cls.RUN_STATE_DIR, state.simulation_id)
        twitter_log = os.path.join(sim_dir, "twitter", "actions.jsonl")
        reddit_log = os.path.join(sim_dir, "reddit", "actions.jsonl")
        
        # Определяем включенные платформы по наличию файлов
        twitter_enabled = os.path.exists(twitter_log)
        reddit_enabled = os.path.exists(reddit_log)
        
        # Если какая-то платформа еще не завершилась, симуляция не завершена
        if twitter_enabled and not state.twitter_completed:
            return False
        if reddit_enabled and not state.reddit_completed:
            return False
        
        # Должна быть завершена хотя бы одна реально включенная платформа
        return twitter_enabled or reddit_enabled
    
    @classmethod
    def _terminate_process(cls, process: subprocess.Popen, simulation_id: str, timeout: int = 10):
        """Кроссплатформенно завершает процесс и его дочерние процессы."""
        if IS_WINDOWS:
            logger.info(f"Завершаю дерево процессов (Windows): simulation={simulation_id}, pid={process.pid}")
            try:
                # Сначала пытаемся остановить мягко
                subprocess.run(
                    ['taskkill', '/PID', str(process.pid), '/T'],
                    capture_output=True,
                    timeout=5
                )
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    logger.warning(f"Процесс не ответил, выполняю принудительное завершение: {simulation_id}")
                    subprocess.run(
                        ['taskkill', '/F', '/PID', str(process.pid), '/T'],
                        capture_output=True,
                        timeout=5
                    )
                    process.wait(timeout=5)
            except Exception as e:
                logger.warning(f"taskkill завершился ошибкой, пробую terminate: {e}")
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
        else:
            # Unix: завершаем всю группу процессов
            pgid = os.getpgid(process.pid)
            logger.info(f"Завершаю группу процессов (Unix): simulation={simulation_id}, pgid={pgid}")
            
            # Сначала отправляем SIGTERM
            os.killpg(pgid, signal.SIGTERM)
            
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                logger.warning(f"Группа процессов не ответила на SIGTERM, отправляю SIGKILL: {simulation_id}")
                os.killpg(pgid, signal.SIGKILL)
                process.wait(timeout=5)
    
    @classmethod
    def stop_simulation(cls, simulation_id: str) -> SimulationRunState:
        """Останавливает симуляцию."""
        state = cls.get_run_state(simulation_id)
        if not state:
            raise ValueError(f"Симуляция не найдена: {simulation_id}")
        
        if state.runner_status not in [RunnerStatus.RUNNING, RunnerStatus.PAUSED]:
            raise ValueError(f"Симуляция не запущена: {simulation_id}, status={state.runner_status}")
        
        state.runner_status = RunnerStatus.STOPPING
        cls._save_run_state(state)
        
        # Останавливаем процесс
        process = cls._processes.get(simulation_id)
        if process and process.poll() is None:
            try:
                cls._terminate_process(process, simulation_id)
            except ProcessLookupError:
                # Процесс уже завершился
                pass
            except Exception as e:
                logger.error(f"Не удалось завершить группу процессов: {simulation_id}, error={e}")
                # Запасной вариант - завершить основной процесс напрямую
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except Exception:
                    process.kill()
        
        state.runner_status = RunnerStatus.STOPPED
        state.twitter_running = False
        state.reddit_running = False
        state.completed_at = datetime.now().isoformat()
        cls._save_run_state(state)
        
        # Останавливаем updater памяти графа
        if cls._graph_memory_enabled.get(simulation_id, False):
            try:
                ZepGraphMemoryManager.stop_updater(simulation_id)
                logger.info(f"Обновление памяти графа остановлено: simulation_id={simulation_id}")
            except Exception as e:
                logger.error(f"Не удалось остановить updater памяти графа: {e}")
            cls._graph_memory_enabled.pop(simulation_id, None)
        
        logger.info(f"Симуляция остановлена: {simulation_id}")
        return state
    
    @classmethod
    def _read_actions_from_file(
        cls,
        file_path: str,
        default_platform: Optional[str] = None,
        platform_filter: Optional[str] = None,
        agent_id: Optional[int] = None,
        round_num: Optional[int] = None
    ) -> List[AgentAction]:
        """
        从单个动作文件中读取动作
        
        Args:
            file_path: 动作日志文件路径
            default_platform: 默认平台（当动作记录中没有 platform 字段时使用）
            platform_filter: 过滤平台
            agent_id: 过滤 Agent ID
            round_num: 过滤轮次
        """
        if not os.path.exists(file_path):
            return []
        
        actions = []
        
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                try:
                    data = json.loads(line)
                    
                    # 跳过非动作记录（如 simulation_start, round_start, round_end 等事件）
                    if "event_type" in data:
                        continue
                    
                    # 跳过没有 agent_id 的记录（非 Agent 动作）
                    if "agent_id" not in data:
                        continue
                    
                    # 获取平台：优先使用记录中的 platform，否则使用默认平台
                    record_platform = data.get("platform") or default_platform or ""
                    
                    # 过滤
                    if platform_filter and record_platform != platform_filter:
                        continue
                    if agent_id is not None and data.get("agent_id") != agent_id:
                        continue
                    if round_num is not None and data.get("round") != round_num:
                        continue
                    
                    actions.append(AgentAction(
                        round_num=data.get("round", 0),
                        timestamp=data.get("timestamp", ""),
                        platform=record_platform,
                        agent_id=data.get("agent_id", 0),
                        agent_name=data.get("agent_name", ""),
                        action_type=data.get("action_type", ""),
                        action_args=data.get("action_args", {}),
                        result=data.get("result"),
                        success=data.get("success", True),
                    ))
                    
                except json.JSONDecodeError:
                    continue
        
        return actions
    
    @classmethod
    def get_all_actions(
        cls,
        simulation_id: str,
        platform: Optional[str] = None,
        agent_id: Optional[int] = None,
        round_num: Optional[int] = None
    ) -> List[AgentAction]:
        """
        获取所有平台的完整动作历史（无分页限制）
        
        Args:
            simulation_id: 模拟ID
            platform: 过滤平台（twitter/reddit）
            agent_id: 过滤Agent
            round_num: 过滤轮次
            
        Returns:
            完整的动作列表（按时间戳排序，新的在前）
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        actions = []
        
        # 读取 Twitter 动作文件（根据文件路径自动设置 platform 为 twitter）
        twitter_actions_log = os.path.join(sim_dir, "twitter", "actions.jsonl")
        if not platform or platform == "twitter":
            actions.extend(cls._read_actions_from_file(
                twitter_actions_log,
                default_platform="twitter",  # 自动填充 platform 字段
                platform_filter=platform,
                agent_id=agent_id, 
                round_num=round_num
            ))
        
        # 读取 Reddit 动作文件（根据文件路径自动设置 platform 为 reddit）
        reddit_actions_log = os.path.join(sim_dir, "reddit", "actions.jsonl")
        if not platform or platform == "reddit":
            actions.extend(cls._read_actions_from_file(
                reddit_actions_log,
                default_platform="reddit",  # 自动填充 platform 字段
                platform_filter=platform,
                agent_id=agent_id,
                round_num=round_num
            ))
        
        # 如果分平台文件不存在，尝试读取旧的单一文件格式
        if not actions:
            actions_log = os.path.join(sim_dir, "actions.jsonl")
            actions = cls._read_actions_from_file(
                actions_log,
                default_platform=None,  # 旧格式文件中应该有 platform 字段
                platform_filter=platform,
                agent_id=agent_id,
                round_num=round_num
            )
        
        # 按时间戳排序（新的在前）
        actions.sort(key=lambda x: x.timestamp, reverse=True)
        
        return actions
    
    @classmethod
    def get_actions(
        cls,
        simulation_id: str,
        limit: int = 100,
        offset: int = 0,
        platform: Optional[str] = None,
        agent_id: Optional[int] = None,
        round_num: Optional[int] = None
    ) -> List[AgentAction]:
        """
        获取动作历史（带分页）
        
        Args:
            simulation_id: 模拟ID
            limit: 返回数量限制
            offset: 偏移量
            platform: 过滤平台
            agent_id: 过滤Agent
            round_num: 过滤轮次
            
        Returns:
            动作列表
        """
        actions = cls.get_all_actions(
            simulation_id=simulation_id,
            platform=platform,
            agent_id=agent_id,
            round_num=round_num
        )
        
        # 分页
        return actions[offset:offset + limit]
    
    @classmethod
    def get_timeline(
        cls,
        simulation_id: str,
        start_round: int = 0,
        end_round: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        获取模拟时间线（按轮次汇总）
        
        Args:
            simulation_id: 模拟ID
            start_round: 起始轮次
            end_round: 结束轮次
            
        Returns:
            每轮的汇总信息
        """
        actions = cls.get_actions(simulation_id, limit=10000)
        
        # 按轮次分组
        rounds: Dict[int, Dict[str, Any]] = {}
        
        for action in actions:
            round_num = action.round_num
            
            if round_num < start_round:
                continue
            if end_round is not None and round_num > end_round:
                continue
            
            if round_num not in rounds:
                rounds[round_num] = {
                    "round_num": round_num,
                    "twitter_actions": 0,
                    "reddit_actions": 0,
                    "active_agents": set(),
                    "action_types": {},
                    "first_action_time": action.timestamp,
                    "last_action_time": action.timestamp,
                }
            
            r = rounds[round_num]
            
            if action.platform == "twitter":
                r["twitter_actions"] += 1
            else:
                r["reddit_actions"] += 1
            
            r["active_agents"].add(action.agent_id)
            r["action_types"][action.action_type] = r["action_types"].get(action.action_type, 0) + 1
            r["last_action_time"] = action.timestamp
        
        # 转换为列表
        result = []
        for round_num in sorted(rounds.keys()):
            r = rounds[round_num]
            result.append({
                "round_num": round_num,
                "twitter_actions": r["twitter_actions"],
                "reddit_actions": r["reddit_actions"],
                "total_actions": r["twitter_actions"] + r["reddit_actions"],
                "active_agents_count": len(r["active_agents"]),
                "active_agents": list(r["active_agents"]),
                "action_types": r["action_types"],
                "first_action_time": r["first_action_time"],
                "last_action_time": r["last_action_time"],
            })
        
        return result
    
    @classmethod
    def get_agent_stats(cls, simulation_id: str) -> List[Dict[str, Any]]:
        """
        获取每个Agent的统计信息
        
        Returns:
            Agent统计列表
        """
        actions = cls.get_actions(simulation_id, limit=10000)
        
        agent_stats: Dict[int, Dict[str, Any]] = {}
        
        for action in actions:
            agent_id = action.agent_id
            
            if agent_id not in agent_stats:
                agent_stats[agent_id] = {
                    "agent_id": agent_id,
                    "agent_name": action.agent_name,
                    "total_actions": 0,
                    "twitter_actions": 0,
                    "reddit_actions": 0,
                    "action_types": {},
                    "first_action_time": action.timestamp,
                    "last_action_time": action.timestamp,
                }
            
            stats = agent_stats[agent_id]
            stats["total_actions"] += 1
            
            if action.platform == "twitter":
                stats["twitter_actions"] += 1
            else:
                stats["reddit_actions"] += 1
            
            stats["action_types"][action.action_type] = stats["action_types"].get(action.action_type, 0) + 1
            stats["last_action_time"] = action.timestamp
        
        # 按总动作数排序
        result = sorted(agent_stats.values(), key=lambda x: x["total_actions"], reverse=True)
        
        return result
    
    @classmethod
    def cleanup_simulation_logs(cls, simulation_id: str) -> Dict[str, Any]:
        """
        清理模拟的运行日志（用于强制重新开始模拟）
        
        会删除以下文件：
        - run_state.json
        - twitter/actions.jsonl
        - reddit/actions.jsonl
        - simulation.log
        - stdout.log / stderr.log
        - twitter_simulation.db（模拟数据库）
        - reddit_simulation.db（模拟数据库）
        - env_status.json（环境状态）
        
        注意：不会删除配置文件（simulation_config.json）和 profile 文件
        
        Args:
            simulation_id: 模拟ID
            
        Returns:
            清理结果信息
        """
        import shutil
        
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        
        if not os.path.exists(sim_dir):
            return {"success": True, "message": "Каталог симуляции отсутствует, очищать нечего"}
        
        cleaned_files = []
        errors = []
        
        # 要删除的文件列表（包括数据库文件）
        files_to_delete = [
            "run_state.json",
            "simulation.log",
            "stdout.log",
            "stderr.log",
            "twitter_simulation.db",  # Twitter 平台数据库
            "reddit_simulation.db",   # Reddit 平台数据库
            "env_status.json",        # 环境状态文件
        ]
        
        # 要删除的目录列表（包含动作日志）
        dirs_to_clean = ["twitter", "reddit"]
        
        # 删除文件
        for filename in files_to_delete:
            file_path = os.path.join(sim_dir, filename)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    cleaned_files.append(filename)
                except Exception as e:
                    errors.append(f"Не удалось удалить {filename}: {str(e)}")
        
        # 清理平台目录中的动作日志
        for dir_name in dirs_to_clean:
            dir_path = os.path.join(sim_dir, dir_name)
            if os.path.exists(dir_path):
                actions_file = os.path.join(dir_path, "actions.jsonl")
                if os.path.exists(actions_file):
                    try:
                        os.remove(actions_file)
                        cleaned_files.append(f"{dir_name}/actions.jsonl")
                    except Exception as e:
                        errors.append(f"Не удалось удалить {dir_name}/actions.jsonl: {str(e)}")
        
        # 清理内存中的运行状态
        if simulation_id in cls._run_states:
            del cls._run_states[simulation_id]
        
        logger.info(f"Очистка логов симуляции завершена: {simulation_id}, удалены файлы: {cleaned_files}")
        
        return {
            "success": len(errors) == 0,
            "cleaned_files": cleaned_files,
            "errors": errors if errors else None
        }
    
    # 防止重复清理的标志
    _cleanup_done = False
    
    @classmethod
    def cleanup_all_simulations(cls):
        """
        清理所有运行中的模拟进程
        
        在服务器关闭时调用，确保所有子进程被终止
        """
        # 防止重复清理
        if cls._cleanup_done:
            return
        cls._cleanup_done = True
        
        # 检查是否有内容需要清理（避免空进程的进程打印无用日志）
        has_processes = bool(cls._processes)
        has_updaters = bool(cls._graph_memory_enabled)
        
        if not has_processes and not has_updaters:
            return  # 没有需要清理的内容，静默返回
        
        logger.info("Начинаю очистку всех процессов симуляции...")
        
        # 首先停止所有图谱记忆更新器（stop_all 内部会打印日志）
        try:
            ZepGraphMemoryManager.stop_all()
        except Exception as e:
            logger.error(f"Не удалось остановить updater памяти графа: {e}")
        cls._graph_memory_enabled.clear()
        
        # 复制字典以避免在迭代时修改
        processes = list(cls._processes.items())
        
        for simulation_id, process in processes:
            try:
                if process.poll() is None:  # 进程仍在运行
                    logger.info(f"Завершаю процесс симуляции: {simulation_id}, pid={process.pid}")
                    
                    try:
                        # 使用跨平台的进程终止方法
                        cls._terminate_process(process, simulation_id, timeout=5)
                    except (ProcessLookupError, OSError):
                        # 进程可能已经不存在，尝试直接终止
                        try:
                            process.terminate()
                            process.wait(timeout=3)
                        except Exception:
                            process.kill()
                    
                    # 更新 run_state.json
                    state = cls.get_run_state(simulation_id)
                    if state:
                        state.runner_status = RunnerStatus.STOPPED
                        state.twitter_running = False
                        state.reddit_running = False
                        state.completed_at = datetime.now().isoformat()
                        state.error = "Сервер остановлен, симуляция принудительно завершена"
                        cls._save_run_state(state)
                    
                    # 同时更新 state.json，将状态设为 stopped
                    try:
                        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
                        state_file = os.path.join(sim_dir, "state.json")
                        logger.info(f"Пытаюсь обновить state.json: {state_file}")
                        if os.path.exists(state_file):
                            with open(state_file, 'r', encoding='utf-8') as f:
                                state_data = json.load(f)
                            state_data['status'] = 'stopped'
                            state_data['updated_at'] = datetime.now().isoformat()
                            with open(state_file, 'w', encoding='utf-8') as f:
                                json.dump(state_data, f, indent=2, ensure_ascii=False)
                            logger.info(f"state.json обновлен со статусом stopped: {simulation_id}")
                        else:
                            logger.warning(f"state.json отсутствует: {state_file}")
                    except Exception as state_err:
                        logger.warning(f"Не удалось обновить state.json: {simulation_id}, error={state_err}")
                        
            except Exception as e:
                logger.error(f"Не удалось очистить процесс: {simulation_id}, error={e}")
        
        # 清理文件句柄
        for simulation_id, file_handle in list(cls._stdout_files.items()):
            try:
                if file_handle:
                    file_handle.close()
            except Exception:
                pass
        cls._stdout_files.clear()
        
        for simulation_id, file_handle in list(cls._stderr_files.items()):
            try:
                if file_handle:
                    file_handle.close()
            except Exception:
                pass
        cls._stderr_files.clear()
        
        # 清理内存中的状态
        cls._processes.clear()
        cls._action_queues.clear()
        
        logger.info("Очистка процессов симуляции завершена")
    
    @classmethod
    def register_cleanup(cls):
        """
        注册清理函数
        
        在 Flask 应用启动时调用，确保服务器关闭时清理所有模拟进程
        """
        global _cleanup_registered
        
        if _cleanup_registered:
            return
        
        # Flask debug 模式下，只在 reloader 子进程中注册清理（实际运行应用的进程）
        # WERKZEUG_RUN_MAIN=true 表示是 reloader 子进程
        # 如果不是 debug 模式，则没有这个环境变量，也需要注册
        is_reloader_process = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
        is_debug_mode = os.environ.get('FLASK_DEBUG') == '1' or os.environ.get('WERKZEUG_RUN_MAIN') is not None
        
        # 在 debug 模式下，只在 reloader 子进程中注册；非 debug 模式下始终注册
        if is_debug_mode and not is_reloader_process:
            _cleanup_registered = True  # 标记已注册，防止子进程再次尝试
            return
        
        # 保存原有的信号处理器
        original_sigint = signal.getsignal(signal.SIGINT)
        original_sigterm = signal.getsignal(signal.SIGTERM)
        # SIGHUP 只在 Unix 系统存在（macOS/Linux），Windows 没有
        original_sighup = None
        has_sighup = hasattr(signal, 'SIGHUP')
        if has_sighup:
            original_sighup = signal.getsignal(signal.SIGHUP)
        
        def cleanup_handler(signum=None, frame=None):
            """Обработчик сигнала: сначала очищает процессы симуляции, потом вызывает исходный."""
            # 只有在有进程需要清理时才打印日志
            if cls._processes or cls._graph_memory_enabled:
                logger.info(f"Получен сигнал {signum}, начинаю очистку...")
            cls.cleanup_all_simulations()
            
            # 调用原有的信号处理器，让 Flask 正常退出
            if signum == signal.SIGINT and callable(original_sigint):
                original_sigint(signum, frame)
            elif signum == signal.SIGTERM and callable(original_sigterm):
                original_sigterm(signum, frame)
            elif has_sighup and signum == signal.SIGHUP:
                # SIGHUP: 终端关闭时发送
                if callable(original_sighup):
                    original_sighup(signum, frame)
                else:
                    # 默认行为：正常退出
                    sys.exit(0)
            else:
                # 如果原处理器不可调用（如 SIG_DFL），则使用默认行为
                raise KeyboardInterrupt
        
        # 注册 atexit 处理器（作为备用）
        atexit.register(cls.cleanup_all_simulations)
        
        # 注册信号处理器（仅在主线程中）
        try:
            # SIGTERM: kill 命令默认信号
            signal.signal(signal.SIGTERM, cleanup_handler)
            # SIGINT: Ctrl+C
            signal.signal(signal.SIGINT, cleanup_handler)
            # SIGHUP: 终端关闭（仅 Unix 系统）
            if has_sighup:
                signal.signal(signal.SIGHUP, cleanup_handler)
        except ValueError:
            # 不在主线程中，只能使用 atexit
            logger.warning("Не удалось зарегистрировать обработчик сигналов вне главного потока, использую только atexit")
        
        _cleanup_registered = True
    
    @classmethod
    def get_running_simulations(cls) -> List[str]:
        """
        获取所有正在运行的模拟ID列表
        """
        running = []
        for sim_id, process in cls._processes.items():
            if process.poll() is None:
                running.append(sim_id)
        return running
    
    # ============== Interview 功能 ==============
    
    @classmethod
    def check_env_alive(cls, simulation_id: str) -> bool:
        """
        检查模拟环境是否存活（可以接收Interview命令）

        Args:
            simulation_id: 模拟ID

        Returns:
            True 表示环境存活，False 表示环境已关闭
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            return False

        ipc_client = SimulationIPCClient(sim_dir)
        return ipc_client.check_env_alive()

    @classmethod
    def get_env_status_detail(cls, simulation_id: str) -> Dict[str, Any]:
        """
        获取模拟环境的详细状态信息

        Args:
            simulation_id: 模拟ID

        Returns:
            状态详情字典，包含 status, twitter_available, reddit_available, timestamp
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        status_file = os.path.join(sim_dir, "env_status.json")
        
        default_status = {
            "status": "stopped",
            "twitter_available": False,
            "reddit_available": False,
            "timestamp": None
        }
        
        if not os.path.exists(status_file):
            return default_status
        
        try:
            with open(status_file, 'r', encoding='utf-8') as f:
                status = json.load(f)
            return {
                "status": status.get("status", "stopped"),
                "twitter_available": status.get("twitter_available", False),
                "reddit_available": status.get("reddit_available", False),
                "timestamp": status.get("timestamp")
            }
        except (json.JSONDecodeError, OSError):
            return default_status

    @classmethod
    def interview_agent(
        cls,
        simulation_id: str,
        agent_id: int,
        prompt: str,
        platform: str = None,
        timeout: float = 60.0
    ) -> Dict[str, Any]:
        """
        采访单个Agent

        Args:
            simulation_id: 模拟ID
            agent_id: Agent ID
            prompt: 采访问题
            platform: 指定平台（可选）
                - "twitter": 只采访Twitter平台
                - "reddit": 只采访Reddit平台
                - None: 双平台模拟时同时采访两个平台，返回整合结果
            timeout: 超时时间（秒）

        Returns:
            采访结果字典

        Raises:
            ValueError: 模拟不存在或环境未运行
            TimeoutError: 等待响应超时
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            raise ValueError(f"Симуляция не найдена: {simulation_id}")

        ipc_client = SimulationIPCClient(sim_dir)

        if not ipc_client.check_env_alive():
            raise ValueError(f"Среда симуляции не запущена или уже закрыта, интервью недоступно: {simulation_id}")

        logger.info(f"Отправляю команду интервью: simulation_id={simulation_id}, agent_id={agent_id}, platform={platform}")

        response = ipc_client.send_interview(
            agent_id=agent_id,
            prompt=prompt,
            platform=platform,
            timeout=timeout
        )

        if response.status.value == "completed":
            return {
                "success": True,
                "agent_id": agent_id,
                "prompt": prompt,
                "result": response.result,
                "timestamp": response.timestamp
            }
        else:
            return {
                "success": False,
                "agent_id": agent_id,
                "prompt": prompt,
                "error": response.error,
                "timestamp": response.timestamp
            }
    
    @classmethod
    def interview_agents_batch(
        cls,
        simulation_id: str,
        interviews: List[Dict[str, Any]],
        platform: str = None,
        timeout: float = 120.0
    ) -> Dict[str, Any]:
        """
        批量采访多个Agent

        Args:
            simulation_id: 模拟ID
            interviews: 采访列表，每个元素包含 {"agent_id": int, "prompt": str, "platform": str(可选)}
            platform: 默认平台（可选，会被每个采访项的platform覆盖）
                - "twitter": 默认只采访Twitter平台
                - "reddit": 默认只采访Reddit平台
                - None: 双平台模拟时每个Agent同时采访两个平台
            timeout: 超时时间（秒）

        Returns:
            批量采访结果字典

        Raises:
            ValueError: 模拟不存在或环境未运行
            TimeoutError: 等待响应超时
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            raise ValueError(f"Симуляция не найдена: {simulation_id}")

        ipc_client = SimulationIPCClient(sim_dir)

        if not ipc_client.check_env_alive():
            raise ValueError(f"Среда симуляции не запущена или уже закрыта, пакетное интервью недоступно: {simulation_id}")

        logger.info(f"Отправляю пакетное интервью: simulation_id={simulation_id}, count={len(interviews)}, platform={platform}")

        response = ipc_client.send_batch_interview(
            interviews=interviews,
            platform=platform,
            timeout=timeout
        )

        if response.status.value == "completed":
            return {
                "success": True,
                "interviews_count": len(interviews),
                "result": response.result,
                "timestamp": response.timestamp
            }
        else:
            return {
                "success": False,
                "interviews_count": len(interviews),
                "error": response.error,
                "timestamp": response.timestamp
            }
    
    @classmethod
    def interview_all_agents(
        cls,
        simulation_id: str,
        prompt: str,
        platform: str = None,
        timeout: float = 180.0
    ) -> Dict[str, Any]:
        """
        采访所有Agent（全局采访）

        使用相同的问题采访模拟中的所有Agent

        Args:
            simulation_id: 模拟ID
            prompt: 采访问题（所有Agent使用相同问题）
            platform: 指定平台（可选）
                - "twitter": 只采访Twitter平台
                - "reddit": 只采访Reddit平台
                - None: 双平台模拟时每个Agent同时采访两个平台
            timeout: 超时时间（秒）

        Returns:
            全局采访结果字典
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            raise ValueError(f"Симуляция не найдена: {simulation_id}")

        # 从配置文件获取所有Agent信息
        config_path = os.path.join(sim_dir, "simulation_config.json")
        if not os.path.exists(config_path):
            raise ValueError(f"Конфигурация симуляции не найдена: {simulation_id}")

        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)

        agent_configs = config.get("agent_configs", [])
        if not agent_configs:
            raise ValueError(f"В конфигурации симуляции нет агентов: {simulation_id}")

        # 构建批量采访列表
        interviews = []
        for agent_config in agent_configs:
            agent_id = agent_config.get("agent_id")
            if agent_id is not None:
                interviews.append({
                    "agent_id": agent_id,
                    "prompt": prompt
                })

        logger.info(f"Отправляю глобальное интервью: simulation_id={simulation_id}, agent_count={len(interviews)}, platform={platform}")

        return cls.interview_agents_batch(
            simulation_id=simulation_id,
            interviews=interviews,
            platform=platform,
            timeout=timeout
        )
    
    @classmethod
    def close_simulation_env(
        cls,
        simulation_id: str,
        timeout: float = 30.0
    ) -> Dict[str, Any]:
        """
        关闭模拟环境（而不是停止模拟进程）
        
        向模拟发送关闭环境命令，使其优雅退出等待命令模式
        
        Args:
            simulation_id: 模拟ID
            timeout: 超时时间（秒）
            
        Returns:
            操作结果字典
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        if not os.path.exists(sim_dir):
            raise ValueError(f"Симуляция не найдена: {simulation_id}")
        
        ipc_client = SimulationIPCClient(sim_dir)
        
        if not ipc_client.check_env_alive():
            return {
                "success": True,
                "message": "Среда уже закрыта"
            }
        
        logger.info(f"Отправляю команду закрытия среды: simulation_id={simulation_id}")
        
        try:
            response = ipc_client.send_close_env(timeout=timeout)
            
            return {
                "success": response.status.value == "completed",
                "message": "Команда закрытия среды отправлена",
                "result": response.result,
                "timestamp": response.timestamp
            }
        except TimeoutError:
            # 超时可能是因为环境正在关闭
            return {
                "success": True,
                "message": "Команда закрытия среды отправлена, ожидание ответа истекло; среда, вероятно, закрывается"
            }
    
    @classmethod
    def _get_interview_history_from_db(
        cls,
        db_path: str,
        platform_name: str,
        agent_id: Optional[int] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Читает историю интервью из одной базы данных."""
        import sqlite3
        
        if not os.path.exists(db_path):
            return []
        
        results = []
        
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            if agent_id is not None:
                cursor.execute("""
                    SELECT user_id, info, created_at
                    FROM trace
                    WHERE action = 'interview' AND user_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (agent_id, limit))
            else:
                cursor.execute("""
                    SELECT user_id, info, created_at
                    FROM trace
                    WHERE action = 'interview'
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (limit,))
            
            for user_id, info_json, created_at in cursor.fetchall():
                try:
                    info = json.loads(info_json) if info_json else {}
                except json.JSONDecodeError:
                    info = {"raw": info_json}
                
                results.append({
                    "agent_id": user_id,
                    "response": info.get("response", info),
                    "prompt": info.get("prompt", ""),
                    "timestamp": created_at,
                    "platform": platform_name
                })
            
            conn.close()
            
        except Exception as e:
            logger.error(f"Не удалось прочитать историю интервью ({platform_name}): {e}")
        
        return results

    @classmethod
    def get_interview_history(
        cls,
        simulation_id: str,
        platform: str = None,
        agent_id: Optional[int] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        获取Interview历史记录（从数据库读取）
        
        Args:
            simulation_id: 模拟ID
            platform: 平台类型（reddit/twitter/None）
                - "reddit": 只获取Reddit平台的历史
                - "twitter": 只获取Twitter平台的历史
                - None: 获取两个平台的所有历史
            agent_id: 指定Agent ID（可选，只获取该Agent的历史）
            limit: 每个平台返回数量限制
            
        Returns:
            Interview历史记录列表
        """
        sim_dir = os.path.join(cls.RUN_STATE_DIR, simulation_id)
        
        results = []
        
        # 确定要查询的平台
        if platform in ("reddit", "twitter"):
            platforms = [platform]
        else:
            # 不指定platform时，查询两个平台
            platforms = ["twitter", "reddit"]
        
        for p in platforms:
            db_path = os.path.join(sim_dir, f"{p}_simulation.db")
            platform_results = cls._get_interview_history_from_db(
                db_path=db_path,
                platform_name=p,
                agent_id=agent_id,
                limit=limit
            )
            results.extend(platform_results)
        
        # 按时间降序排序
        results.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        
        # 如果查询了多个平台，限制总数
        if len(platforms) > 1 and len(results) > limit:
            results = results[:limit]
        
        return results
