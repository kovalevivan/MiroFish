"""
Интеллектуальный генератор конфигурации симуляции.

Использует LLM, чтобы по описанию сценария, текстам документов и данным графа
автоматически собрать детальные параметры симуляции без ручной настройки.

Генерация разбита на этапы, чтобы не упираться в слишком длинные ответы модели:
1. временная конфигурация;
2. конфигурация событий;
3. пакетная генерация параметров агентов;
4. настройки платформ.
"""

import json
import math
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field, asdict
from datetime import datetime

from openai import OpenAI

from ..config import Config
from ..utils.logger import get_logger
from .zep_entity_reader import EntityNode, ZepEntityReader

logger = get_logger('mirofish.simulation_config')

# Конфигурация суточной активности по московскому времени
CHINA_TIMEZONE_CONFIG = {
    # Глубокая ночь
    "dead_hours": [0, 1, 2, 3, 4, 5],
    # Утро
    "morning_hours": [6, 7, 8],
    # Рабочие часы
    "work_hours": [9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
    # Вечерний пик
    "peak_hours": [19, 20, 21, 22],
    # Поздний вечер
    "night_hours": [23],
    # Коэффициенты активности
    "activity_multipliers": {
        "dead": 0.05,
        "morning": 0.4,
        "work": 0.7,
        "peak": 1.5,
        "night": 0.5
    }
}


@dataclass
class AgentActivityConfig:
    """Параметры активности одного агента."""
    agent_id: int
    entity_uuid: str
    entity_name: str
    entity_type: str
    
    # Общая активность (0.0-1.0)
    activity_level: float = 0.5
    
    # Частота публикаций в час
    posts_per_hour: float = 1.0
    comments_per_hour: float = 2.0
    
    # Активные часы (24-часовой формат)
    active_hours: List[int] = field(default_factory=lambda: list(range(8, 23)))
    
    # Скорость реакции на инфоповод (в минутах симуляции)
    response_delay_min: int = 5
    response_delay_max: int = 60
    
    # Эмоциональный уклон (-1.0..1.0)
    sentiment_bias: float = 0.0
    
    # Позиция по теме
    stance: str = "neutral"  # supportive, opposing, neutral, observer
    
    # Вес влияния
    influence_weight: float = 1.0


@dataclass  
class TimeSimulationConfig:
    """Параметры времени в симуляции."""
    # Общая длительность симуляции
    total_simulation_hours: int = 72
    
    # Сколько минут симуляции проходит за раунд
    minutes_per_round: int = 60
    
    # Диапазон числа активных агентов в час
    agents_per_hour_min: int = 5
    agents_per_hour_max: int = 20
    
    # Пиковые часы
    peak_hours: List[int] = field(default_factory=lambda: [19, 20, 21, 22])
    peak_activity_multiplier: float = 1.5
    
    # Низкая активность ночью
    off_peak_hours: List[int] = field(default_factory=lambda: [0, 1, 2, 3, 4, 5])
    off_peak_activity_multiplier: float = 0.05
    
    # Утренние часы
    morning_hours: List[int] = field(default_factory=lambda: [6, 7, 8])
    morning_activity_multiplier: float = 0.4
    
    # Рабочее время
    work_hours: List[int] = field(default_factory=lambda: [9, 10, 11, 12, 13, 14, 15, 16, 17, 18])
    work_activity_multiplier: float = 0.7


@dataclass
class EventConfig:
    """Настройки событий."""
    # Стартовые события
    initial_posts: List[Dict[str, Any]] = field(default_factory=list)
    
    # Запланированные события
    scheduled_events: List[Dict[str, Any]] = field(default_factory=list)
    
    # Ключевые темы
    hot_topics: List[str] = field(default_factory=list)
    
    # Основной нарратив
    narrative_direction: str = ""


@dataclass
class PlatformConfig:
    """Параметры отдельной платформы."""
    platform: str  # twitter or reddit
    
    # Веса в рекомендательной логике
    recency_weight: float = 0.4
    popularity_weight: float = 0.3
    relevance_weight: float = 0.3
    
    # Порог вирусного распространения
    viral_threshold: int = 10
    
    # Сила эффекта эхо-камеры
    echo_chamber_strength: float = 0.5


@dataclass
class SimulationParameters:
    """Полная конфигурация симуляции."""
    # Базовая информация
    simulation_id: str
    project_id: str
    graph_id: str
    simulation_requirement: str
    
    # Временные параметры
    time_config: TimeSimulationConfig = field(default_factory=TimeSimulationConfig)
    
    # Конфигурации агентов
    agent_configs: List[AgentActivityConfig] = field(default_factory=list)
    
    # События
    event_config: EventConfig = field(default_factory=EventConfig)
    
    # Параметры платформ
    twitter_config: Optional[PlatformConfig] = None
    reddit_config: Optional[PlatformConfig] = None
    
    # Параметры LLM
    llm_model: str = ""
    llm_base_url: str = ""
    
    # Метаданные генерации
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    generation_reasoning: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        """Преобразует объект в словарь."""
        time_dict = asdict(self.time_config)
        return {
            "simulation_id": self.simulation_id,
            "project_id": self.project_id,
            "graph_id": self.graph_id,
            "simulation_requirement": self.simulation_requirement,
            "time_config": time_dict,
            "agent_configs": [asdict(a) for a in self.agent_configs],
            "event_config": asdict(self.event_config),
            "twitter_config": asdict(self.twitter_config) if self.twitter_config else None,
            "reddit_config": asdict(self.reddit_config) if self.reddit_config else None,
            "llm_model": self.llm_model,
            "llm_base_url": self.llm_base_url,
            "generated_at": self.generated_at,
            "generation_reasoning": self.generation_reasoning,
        }
    
    def to_json(self, indent: int = 2) -> str:
        """Преобразует объект в JSON-строку."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


class SimulationConfigGenerator:
    """
    Генерирует конфигурацию симуляции на основе LLM.

    Анализирует описание сценария, документы и сущности графа и поэтапно
    собирает итоговые параметры:
    1. время и события;
    2. конфигурации агентов пакетами;
    3. настройки платформ.
    """
    
    # Максимальный размер контекста
    MAX_CONTEXT_LENGTH = 50000
    # Размер пакета агентов
    AGENTS_PER_BATCH = 15
    
    # Ограничения длины контекста по этапам
    TIME_CONFIG_CONTEXT_LENGTH = 10000
    EVENT_CONFIG_CONTEXT_LENGTH = 8000
    ENTITY_SUMMARY_LENGTH = 300
    AGENT_SUMMARY_LENGTH = 300
    ENTITIES_PER_TYPE_DISPLAY = 20
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None
    ):
        self.api_key = api_key or Config.LLM_API_KEY
        self.base_url = base_url or Config.LLM_BASE_URL
        self.model_name = model_name or Config.LLM_MODEL_NAME
        
        if not self.api_key:
            raise ValueError("LLM_API_KEY не настроен")
        
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )
    
    def generate_config(
        self,
        simulation_id: str,
        project_id: str,
        graph_id: str,
        simulation_requirement: str,
        document_text: str,
        entities: List[EntityNode],
        enable_twitter: bool = True,
        enable_reddit: bool = True,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> SimulationParameters:
        """
        Пошагово генерирует полную конфигурацию симуляции.

        Args:
            simulation_id: ID симуляции
            project_id: ID проекта
            graph_id: ID графа
            simulation_requirement: описание задачи симуляции
            document_text: исходный текст документов
            entities: отфильтрованные сущности
            enable_twitter: включать ли платформу twitter
            enable_reddit: включать ли платформу reddit
            progress_callback: callback прогресса

        Returns:
            Полная структура SimulationParameters
        """
        logger.info(f"Начинаю интеллектуальную генерацию конфигурации симуляции: simulation_id={simulation_id}, entities={len(entities)}")
        
        # Считаем общее количество шагов
        num_batches = math.ceil(len(entities) / self.AGENTS_PER_BATCH)
        total_steps = 3 + num_batches  # время + события + N пакетов агентов + платформы
        current_step = 0
        
        def report_progress(step: int, message: str):
            nonlocal current_step
            current_step = step
            if progress_callback:
                progress_callback(step, total_steps, message)
            logger.info(f"[{step}/{total_steps}] {message}")
        
        # 1. Собираем базовый контекст
        context = self._build_context(
            simulation_requirement=simulation_requirement,
            document_text=document_text,
            entities=entities
        )
        
        reasoning_parts = []
        
        # ========== Шаг 1: временная конфигурация ==========
        report_progress(1, "Генерация временной конфигурации...")
        num_entities = len(entities)
        time_config_result = self._generate_time_config(context, num_entities)
        time_config = self._parse_time_config(time_config_result, num_entities)
        reasoning_parts.append(f"Временная конфигурация: {time_config_result.get('reasoning', 'успешно')}")
        
        # ========== Шаг 2: события и горячие темы ==========
        report_progress(2, "Генерация событийной конфигурации и горячих тем...")
        event_config_result = self._generate_event_config(context, simulation_requirement, entities)
        event_config = self._parse_event_config(event_config_result)
        reasoning_parts.append(f"Событийная конфигурация: {event_config_result.get('reasoning', 'успешно')}")
        
        # ========== Шаги 3-N: пакетная генерация агентов ==========
        all_agent_configs = []
        for batch_idx in range(num_batches):
            start_idx = batch_idx * self.AGENTS_PER_BATCH
            end_idx = min(start_idx + self.AGENTS_PER_BATCH, len(entities))
            batch_entities = entities[start_idx:end_idx]
            
            report_progress(
                3 + batch_idx,
                f"Генерация конфигураций агентов ({start_idx + 1}-{end_idx}/{len(entities)})..."
            )
            
            batch_configs = self._generate_agent_configs_batch(
                context=context,
                entities=batch_entities,
                start_idx=start_idx,
                simulation_requirement=simulation_requirement
            )
            all_agent_configs.extend(batch_configs)
        
        reasoning_parts.append(f"Конфигурации агентов: успешно создано {len(all_agent_configs)}")
        
        # ========== Назначаем авторов стартовых публикаций ==========
        logger.info("Назначаю подходящих агентов-авторов для стартовых публикаций...")
        event_config = self._assign_initial_post_agents(event_config, all_agent_configs)
        assigned_count = len([p for p in event_config.initial_posts if p.get("poster_agent_id") is not None])
        reasoning_parts.append(f"Назначение стартовых публикаций: автор назначен для {assigned_count} постов")
        
        # ========== Финальный шаг: конфигурация платформ ==========
        report_progress(total_steps, "Генерация платформенных конфигураций...")
        twitter_config = None
        reddit_config = None
        
        if enable_twitter:
            twitter_config = PlatformConfig(
                platform="twitter",
                recency_weight=0.4,
                popularity_weight=0.3,
                relevance_weight=0.3,
                viral_threshold=10,
                echo_chamber_strength=0.5
            )
        
        if enable_reddit:
            reddit_config = PlatformConfig(
                platform="reddit",
                recency_weight=0.3,
                popularity_weight=0.4,
                relevance_weight=0.3,
                viral_threshold=15,
                echo_chamber_strength=0.6
            )
        
        # Собираем итоговую конфигурацию
        params = SimulationParameters(
            simulation_id=simulation_id,
            project_id=project_id,
            graph_id=graph_id,
            simulation_requirement=simulation_requirement,
            time_config=time_config,
            agent_configs=all_agent_configs,
            event_config=event_config,
            twitter_config=twitter_config,
            reddit_config=reddit_config,
            llm_model=self.model_name,
            llm_base_url=self.base_url,
            generation_reasoning=" | ".join(reasoning_parts)
        )
        
        logger.info(f"Генерация конфигурации симуляции завершена: {len(params.agent_configs)} конфигураций агентов")
        
        return params
    
    def _build_context(
        self,
        simulation_requirement: str,
        document_text: str,
        entities: List[EntityNode]
    ) -> str:
        """Собирает LLM-контекст и обрезает его по лимиту."""
        
        # Сводка по сущностям
        entity_summary = self._summarize_entities(entities)
        
        # Формируем контекст
        context_parts = [
            f"## Требование к симуляции\n{simulation_requirement}",
            f"\n## Информация о сущностях ({len(entities)})\n{entity_summary}",
        ]
        
        current_length = sum(len(p) for p in context_parts)
        remaining_length = self.MAX_CONTEXT_LENGTH - current_length - 500  # Оставляем небольшой запас
        
        if remaining_length > 0 and document_text:
            doc_text = document_text[:remaining_length]
            if len(document_text) > remaining_length:
                doc_text += "\n...(документ был сокращен)"
            context_parts.append(f"\n## Исходное содержимое документов\n{doc_text}")
        
        return "\n".join(context_parts)
    
    def _summarize_entities(self, entities: List[EntityNode]) -> str:
        """Формирует краткую сводку по сущностям."""
        lines = []
        
        # Группируем по типу
        by_type: Dict[str, List[EntityNode]] = {}
        for e in entities:
            t = e.get_entity_type() or "Unknown"
            if t not in by_type:
                by_type[t] = []
            by_type[t].append(e)
        
        for entity_type, type_entities in by_type.items():
            lines.append(f"\n### {entity_type} ({len(type_entities)})")
            # Ограничиваем число выводимых сущностей и длину summary
            display_count = self.ENTITIES_PER_TYPE_DISPLAY
            summary_len = self.ENTITY_SUMMARY_LENGTH
            for e in type_entities[:display_count]:
                summary_preview = (e.summary[:summary_len] + "...") if len(e.summary) > summary_len else e.summary
                lines.append(f"- {e.name}: {summary_preview}")
            if len(type_entities) > display_count:
                lines.append(f"  ... и еще {len(type_entities) - display_count}")
        
        return "\n".join(lines)
    
    def _call_llm_with_retry(self, prompt: str, system_prompt: str) -> Dict[str, Any]:
        """Вызывает LLM с повторами и попыткой починки JSON."""
        import re
        
        max_attempts = 3
        last_error = None
        
        for attempt in range(max_attempts):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.7 - (attempt * 0.1)  # С каждым повтором снижаем температуру
                    # max_tokens не задаем, чтобы не обрезать ответ раньше времени
                )
                
                content = response.choices[0].message.content
                finish_reason = response.choices[0].finish_reason
                
                # Проверяем, не был ли ответ обрезан
                if finish_reason == 'length':
                    logger.warning(f"Вывод LLM был обрезан (attempt {attempt+1})")
                    content = self._fix_truncated_json(content)
                
                # Пытаемся разобрать JSON
                try:
                    return json.loads(content)
                except json.JSONDecodeError as e:
                    logger.warning(f"Не удалось разобрать JSON (attempt {attempt+1}): {str(e)[:80]}")
                    
                    # Пытаемся починить JSON
                    fixed = self._try_fix_config_json(content)
                    if fixed:
                        return fixed
                    
                    last_error = e
                    
            except Exception as e:
                logger.warning(f"Ошибка вызова LLM (attempt {attempt+1}): {str(e)[:80]}")
                last_error = e
                import time
                time.sleep(2 * (attempt + 1))
        
        raise last_error or Exception("Ошибка вызова LLM")
    
    def _fix_truncated_json(self, content: str) -> str:
        """Пытается восстановить обрезанный JSON."""
        content = content.strip()
        
        # 计算未闭合的括号
        open_braces = content.count('{') - content.count('}')
        open_brackets = content.count('[') - content.count(']')
        
        # 检查是否有未闭合的字符串
        if content and content[-1] not in '",}]':
            content += '"'
        
        # 闭合括号
        content += ']' * open_brackets
        content += '}' * open_braces
        
        return content
    
    def _try_fix_config_json(self, content: str) -> Optional[Dict[str, Any]]:
        """Пытается починить JSON конфигурации."""
        import re
        
        # 修复被截断的情况
        content = self._fix_truncated_json(content)
        
        # 提取JSON部分
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            json_str = json_match.group()
            
            # 移除字符串中的换行符
            def fix_string(match):
                s = match.group(0)
                s = s.replace('\n', ' ').replace('\r', ' ')
                s = re.sub(r'\s+', ' ', s)
                return s
            
            json_str = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', fix_string, json_str)
            
            try:
                return json.loads(json_str)
            except:
                # 尝试移除所有控制字符
                json_str = re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', json_str)
                json_str = re.sub(r'\s+', ' ', json_str)
                try:
                    return json.loads(json_str)
                except:
                    pass
        
        return None
    
    def _generate_time_config(self, context: str, num_entities: int) -> Dict[str, Any]:
        """Генерирует временную конфигурацию симуляции."""
        # Используем ограничение контекста из конфигурации
        context_truncated = context[:self.TIME_CONFIG_CONTEXT_LENGTH]
        
        # Максимально допустимое число активируемых агентов в час
        max_agents_allowed = max(1, int(num_entities * 0.9))
        
        prompt = f"""На основе следующего описания симуляции сгенерируй JSON временной конфигурации.

{context_truncated}

## Задача
Верни JSON с временной конфигурацией.

### Базовые ориентиры
- Целевая аудитория живет по московскому времени и использует типичный распорядок дня в России.
- В интервале 0:00-5:00 активность почти отсутствует, коэффициент около 0.05.
- В 6:00-8:00 активность постепенно растет, коэффициент около 0.4.
- В 9:00-18:00 активность средняя, коэффициент около 0.7.
- В 19:00-22:00 обычно наблюдается пик, коэффициент около 1.5.
- После 23:00 активность снижается, коэффициент около 0.5.
- Это только ориентиры: обязательно подстрой интервалы под характер события и типы участников.
- Примеры:
  - у студентов пик может смещаться к 21:00-23:00;
  - медиа могут быть активны почти весь день;
  - официальные структуры активны в рабочие часы;
  - при резком инфоповоде возможны обсуждения и поздно ночью.

### Верни только JSON, без markdown.

Пример:
{{
    "total_simulation_hours": 72,
    "minutes_per_round": 60,
    "agents_per_hour_min": 5,
    "agents_per_hour_max": 50,
    "peak_hours": [19, 20, 21, 22],
    "off_peak_hours": [0, 1, 2, 3, 4, 5],
    "morning_hours": [6, 7, 8],
    "work_hours": [9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
    "reasoning": "краткое объяснение выбранной временной конфигурации"
}}

Поля:
- total_simulation_hours (int): общая длительность симуляции, 24-168 часов;
- minutes_per_round (int): длительность одного раунда, 30-120 минут, обычно 60;
- agents_per_hour_min (int): минимальное число активируемых агентов в час, диапазон 1-{max_agents_allowed};
- agents_per_hour_max (int): максимальное число активируемых агентов в час, диапазон 1-{max_agents_allowed};
- peak_hours (array[int]): часы пиковой активности;
- off_peak_hours (array[int]): часы минимальной активности;
- morning_hours (array[int]): утренние часы;
- work_hours (array[int]): рабочие часы;
- reasoning (string): краткое объяснение конфигурации."""

        system_prompt = "Ты эксперт по симуляции социальных медиа. Верни только JSON. Временная конфигурация должна соответствовать распорядку жизни в России и московскому часовому поясу."
        
        try:
            return self._call_llm_with_retry(prompt, system_prompt)
        except Exception as e:
            logger.warning(f"Не удалось сгенерировать временную конфигурацию через LLM: {e}. Используется конфигурация по умолчанию.")
            return self._get_default_time_config(num_entities)
    
    def _get_default_time_config(self, num_entities: int) -> Dict[str, Any]:
        """Возвращает временную конфигурацию по умолчанию."""
        return {
            "total_simulation_hours": 72,
            "minutes_per_round": 60,  # Один раунд = один час симуляции
            "agents_per_hour_min": max(1, num_entities // 15),
            "agents_per_hour_max": max(5, num_entities // 5),
            "peak_hours": [19, 20, 21, 22],
            "off_peak_hours": [0, 1, 2, 3, 4, 5],
            "morning_hours": [6, 7, 8],
            "work_hours": [9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
            "reasoning": "Используется стандартный распорядок дня для аудитории из России"
        }
    
    def _parse_time_config(self, result: Dict[str, Any], num_entities: int) -> TimeSimulationConfig:
        """Нормализует временную конфигурацию и проверяет допустимость значений."""
        # Получаем исходные значения
        agents_per_hour_min = result.get("agents_per_hour_min", max(1, num_entities // 15))
        agents_per_hour_max = result.get("agents_per_hour_max", max(5, num_entities // 5))
        
        # Проверяем и корректируем, чтобы значения не превышали число агентов
        if agents_per_hour_min > num_entities:
            logger.warning(f"agents_per_hour_min ({agents_per_hour_min}) превышает число агентов ({num_entities}); значение исправлено")
            agents_per_hour_min = max(1, num_entities // 10)
        
        if agents_per_hour_max > num_entities:
            logger.warning(f"agents_per_hour_max ({agents_per_hour_max}) превышает число агентов ({num_entities}); значение исправлено")
            agents_per_hour_max = max(agents_per_hour_min + 1, num_entities // 2)
        
        # Гарантируем min < max
        if agents_per_hour_min >= agents_per_hour_max:
            agents_per_hour_min = max(1, agents_per_hour_max // 2)
            logger.warning(f"agents_per_hour_min >= max; значение скорректировано до {agents_per_hour_min}")
        
        return TimeSimulationConfig(
            total_simulation_hours=result.get("total_simulation_hours", 72),
            minutes_per_round=result.get("minutes_per_round", 60),  # По умолчанию один раунд равен часу
            agents_per_hour_min=agents_per_hour_min,
            agents_per_hour_max=agents_per_hour_max,
            peak_hours=result.get("peak_hours", [19, 20, 21, 22]),
            off_peak_hours=result.get("off_peak_hours", [0, 1, 2, 3, 4, 5]),
            off_peak_activity_multiplier=0.05,  # Ночью активность почти отсутствует
            morning_hours=result.get("morning_hours", [6, 7, 8]),
            morning_activity_multiplier=0.4,
            work_hours=result.get("work_hours", list(range(9, 19))),
            work_activity_multiplier=0.7,
            peak_activity_multiplier=1.5
        )
    
    def _generate_event_config(
        self, 
        context: str, 
        simulation_requirement: str,
        entities: List[EntityNode]
    ) -> Dict[str, Any]:
        """Генерирует конфигурацию событий."""
        
        # Список доступных типов сущностей для LLM
        entity_types_available = list(set(
            e.get_entity_type() or "Unknown" for e in entities
        ))
        
        # Примеры сущностей для каждого типа
        type_examples = {}
        for e in entities:
            etype = e.get_entity_type() or "Unknown"
            if etype not in type_examples:
                type_examples[etype] = []
            if len(type_examples[etype]) < 3:
                type_examples[etype].append(e.name)
        
        type_info = "\n".join([
            f"- {t}: {', '.join(examples)}" 
            for t, examples in type_examples.items()
        ])
        
        # Используем ограничение контекста из конфигурации
        context_truncated = context[:self.EVENT_CONFIG_CONTEXT_LENGTH]
        
        prompt = f"""На основе требований к симуляции сгенерируй JSON-конфигурацию событий.

Требование к симуляции: {simulation_requirement}

{context_truncated}

## Доступные типы сущностей и примеры
{type_info}

## Задача
Сгенерируй JSON-конфигурацию событий:
- выдели ключевые горячие темы;
- опиши направление развития общественной дискуссии;
- предложи стартовые посты, и **для каждого поста обязательно укажи `poster_type`**.

**Важно**: `poster_type` должен быть выбран строго из списка доступных типов выше, чтобы пост можно было назначить подходящему агенту.
Примеры: официальное заявление должно исходить от `Official` или `University`; новость публикует `MediaOutlet`; студенческую позицию публикует `Student`.

Верни только JSON, без markdown:
{{
    "hot_topics": ["тема 1", "тема 2", ...],
    "narrative_direction": "<описание направления развития дискуссии>",
    "initial_posts": [
        {{"content": "текст поста", "poster_type": "тип сущности из доступного списка"}},
        ...
    ],
    "reasoning": "<краткое объяснение>"
}}"""

        system_prompt = "Ты эксперт по анализу общественных дискуссий. Верни только JSON. Поле poster_type должно точно совпадать с одним из доступных типов сущностей."
        
        try:
            return self._call_llm_with_retry(prompt, system_prompt)
        except Exception as e:
            logger.warning(f"Не удалось сгенерировать конфигурацию событий через LLM: {e}. Используется конфигурация по умолчанию.")
            return {
                "hot_topics": [],
                "narrative_direction": "",
                "initial_posts": [],
                "reasoning": "Используется конфигурация по умолчанию"
            }
    
    def _parse_event_config(self, result: Dict[str, Any]) -> EventConfig:
        """Разбирает результат генерации конфигурации событий."""
        return EventConfig(
            initial_posts=result.get("initial_posts", []),
            scheduled_events=[],
            hot_topics=result.get("hot_topics", []),
            narrative_direction=result.get("narrative_direction", "")
        )
    
    def _assign_initial_post_agents(
        self,
        event_config: EventConfig,
        agent_configs: List[AgentActivityConfig]
    ) -> EventConfig:
        """
        Назначает каждому начальному посту подходящего агента-публикатора.

        Подбирает `agent_id` по значению `poster_type`.
        """
        if not event_config.initial_posts:
            return event_config
        
        # Индексируем агентов по типу сущности
        agents_by_type: Dict[str, List[AgentActivityConfig]] = {}
        for agent in agent_configs:
            etype = agent.entity_type.lower()
            if etype not in agents_by_type:
                agents_by_type[etype] = []
            agents_by_type[etype].append(agent)
        
        # Таблица соответствий на случай вариаций в ответе LLM
        type_aliases = {
            "official": ["official", "university", "governmentagency", "government"],
            "university": ["university", "official"],
            "mediaoutlet": ["mediaoutlet", "media"],
            "student": ["student", "person"],
            "professor": ["professor", "expert", "teacher"],
            "alumni": ["alumni", "person"],
            "organization": ["organization", "ngo", "company", "group"],
            "person": ["person", "student", "alumni"],
        }
        
        # Запоминаем, какого агента каждого типа уже использовали
        used_indices: Dict[str, int] = {}
        
        updated_posts = []
        for post in event_config.initial_posts:
            poster_type = post.get("poster_type", "").lower()
            content = post.get("content", "")
            
            # Пытаемся подобрать подходящего агента
            matched_agent_id = None
            
            # 1. Прямое совпадение
            if poster_type in agents_by_type:
                agents = agents_by_type[poster_type]
                idx = used_indices.get(poster_type, 0) % len(agents)
                matched_agent_id = agents[idx].agent_id
                used_indices[poster_type] = idx + 1
            else:
                # 2. Совпадение по алиасам
                for alias_key, aliases in type_aliases.items():
                    if poster_type in aliases or alias_key == poster_type:
                        for alias in aliases:
                            if alias in agents_by_type:
                                agents = agents_by_type[alias]
                                idx = used_indices.get(alias, 0) % len(agents)
                                matched_agent_id = agents[idx].agent_id
                                used_indices[alias] = idx + 1
                                break
                    if matched_agent_id is not None:
                        break
            
            # 3. Если совпадение не найдено, берем наиболее влиятельного агента
            if matched_agent_id is None:
                logger.warning(f"Не найден агент для типа '{poster_type}', будет использован агент с максимальным весом влияния")
                if agent_configs:
                    # Сортируем по весу влияния и берем максимальный
                    sorted_agents = sorted(agent_configs, key=lambda a: a.influence_weight, reverse=True)
                    matched_agent_id = sorted_agents[0].agent_id
                else:
                    matched_agent_id = 0
            
            updated_posts.append({
                "content": content,
                "poster_type": post.get("poster_type", "Unknown"),
                "poster_agent_id": matched_agent_id
            })
            
            logger.info(f"Назначение начального поста: poster_type='{poster_type}' -> agent_id={matched_agent_id}")
        
        event_config.initial_posts = updated_posts
        return event_config
    
    def _generate_agent_configs_batch(
        self,
        context: str,
        entities: List[EntityNode],
        start_idx: int,
        simulation_requirement: str
    ) -> List[AgentActivityConfig]:
        """Генерирует конфигурации агентов пакетами."""
        
        # Формируем данные сущностей с укороченным summary
        entity_list = []
        summary_len = self.AGENT_SUMMARY_LENGTH
        for i, e in enumerate(entities):
            entity_list.append({
                "agent_id": start_idx + i,
                "entity_name": e.name,
                "entity_type": e.get_entity_type() or "Unknown",
                "summary": e.summary[:summary_len] if e.summary else ""
            })
        
        prompt = f"""На основе данных ниже сгенерируй конфигурацию активности в социальных медиа для каждой сущности.

Требование к симуляции: {simulation_requirement}

## Список сущностей
```json
{json.dumps(entity_list, ensure_ascii=False, indent=2)}
```

## Задача
Для каждой сущности сгенерируй конфигурацию активности. Учитывай следующие ориентиры:
- распорядок должен соответствовать московскому часовому поясу и аудитории из России: с 0:00 до 5:00 активность почти отсутствует, а 19:00-22:00 обычно самый активный период;
- **официальные структуры** (`University`, `GovernmentAgency`) малoактивны (0.1-0.3), работают в основном в 9:00-17:00, отвечают медленно (60-240 минут), имеют высокий вес влияния (2.5-3.0);
- **медиа** (`MediaOutlet`) имеют среднюю активность (0.4-0.6), работают почти весь день (8:00-23:00), отвечают быстро (5-30 минут), имеют высокий вес влияния (2.0-2.5);
- **частные лица** (`Student`, `Person`, `Alumni`) активны сильнее (0.6-0.9), чаще всего вечером (18:00-23:00), отвечают быстро (1-15 минут), имеют меньший вес влияния (0.8-1.2);
- **публичные фигуры и эксперты** имеют среднюю активность (0.4-0.6) и средне-высокий вес влияния (1.5-2.0).

Верни только JSON, без markdown:
{{
    "agent_configs": [
        {{
            "agent_id": <должен точно совпадать со входным ID>,
            "activity_level": <0.0-1.0>,
            "posts_per_hour": <частота публикаций>,
            "comments_per_hour": <частота комментариев>,
            "active_hours": [<список активных часов>],
            "response_delay_min": <минимальная задержка ответа в минутах>,
            "response_delay_max": <максимальная задержка ответа в минутах>,
            "sentiment_bias": <-1.0 до 1.0>,
            "stance": "<supportive/opposing/neutral/observer>",
            "influence_weight": <вес влияния>
        }},
        ...
    ]
}}"""

        system_prompt = "Ты эксперт по моделированию поведения в социальных медиа. Верни только JSON. Параметры активности должны соответствовать аудитории из России и московскому часовому поясу."
        
        try:
            result = self._call_llm_with_retry(prompt, system_prompt)
            llm_configs = {cfg["agent_id"]: cfg for cfg in result.get("agent_configs", [])}
        except Exception as e:
            logger.warning(f"Не удалось сгенерировать пакет конфигураций агентов через LLM: {e}. Используется правило-ориентированная генерация.")
            llm_configs = {}
        
        # Собираем объекты AgentActivityConfig
        configs = []
        for i, entity in enumerate(entities):
            agent_id = start_idx + i
            cfg = llm_configs.get(agent_id, {})
            
            # Если LLM не вернула конфигурацию, используем правила
            if not cfg:
                cfg = self._generate_agent_config_by_rule(entity)
            
            config = AgentActivityConfig(
                agent_id=agent_id,
                entity_uuid=entity.uuid,
                entity_name=entity.name,
                entity_type=entity.get_entity_type() or "Unknown",
                activity_level=cfg.get("activity_level", 0.5),
                posts_per_hour=cfg.get("posts_per_hour", 0.5),
                comments_per_hour=cfg.get("comments_per_hour", 1.0),
                active_hours=cfg.get("active_hours", list(range(9, 23))),
                response_delay_min=cfg.get("response_delay_min", 5),
                response_delay_max=cfg.get("response_delay_max", 60),
                sentiment_bias=cfg.get("sentiment_bias", 0.0),
                stance=cfg.get("stance", "neutral"),
                influence_weight=cfg.get("influence_weight", 1.0)
            )
            configs.append(config)
        
        return configs
    
    def _generate_agent_config_by_rule(self, entity: EntityNode) -> Dict[str, Any]:
        """Генерирует конфигурацию агента по правилам."""
        entity_type = (entity.get_entity_type() or "Unknown").lower()
        
        if entity_type in ["university", "governmentagency", "ngo"]:
            # Официальные структуры: рабочее время, низкая частота, высокий вес
            return {
                "activity_level": 0.2,
                "posts_per_hour": 0.1,
                "comments_per_hour": 0.05,
                "active_hours": list(range(9, 18)),  # 9:00-17:59
                "response_delay_min": 60,
                "response_delay_max": 240,
                "sentiment_bias": 0.0,
                "stance": "neutral",
                "influence_weight": 3.0
            }
        elif entity_type in ["mediaoutlet"]:
            # Медиа: почти весь день, средняя частота, высокий вес
            return {
                "activity_level": 0.5,
                "posts_per_hour": 0.8,
                "comments_per_hour": 0.3,
                "active_hours": list(range(7, 24)),  # 7:00-23:59
                "response_delay_min": 5,
                "response_delay_max": 30,
                "sentiment_bias": 0.0,
                "stance": "observer",
                "influence_weight": 2.5
            }
        elif entity_type in ["professor", "expert", "official"]:
            # Эксперты и официальные лица: рабочее время плюс вечер
            return {
                "activity_level": 0.4,
                "posts_per_hour": 0.3,
                "comments_per_hour": 0.5,
                "active_hours": list(range(8, 22)),  # 8:00-21:59
                "response_delay_min": 15,
                "response_delay_max": 90,
                "sentiment_bias": 0.0,
                "stance": "neutral",
                "influence_weight": 2.0
            }
        elif entity_type in ["student"]:
            # Студенты: в основном активны вечером
            return {
                "activity_level": 0.8,
                "posts_per_hour": 0.6,
                "comments_per_hour": 1.5,
                "active_hours": [8, 9, 10, 11, 12, 13, 18, 19, 20, 21, 22, 23],  # День и вечер
                "response_delay_min": 1,
                "response_delay_max": 15,
                "sentiment_bias": 0.0,
                "stance": "neutral",
                "influence_weight": 0.8
            }
        elif entity_type in ["alumni"]:
            # Выпускники: преимущественно вечерняя активность
            return {
                "activity_level": 0.6,
                "posts_per_hour": 0.4,
                "comments_per_hour": 0.8,
                "active_hours": [12, 13, 19, 20, 21, 22, 23],  # День и вечер
                "response_delay_min": 5,
                "response_delay_max": 30,
                "sentiment_bias": 0.0,
                "stance": "neutral",
                "influence_weight": 1.0
            }
        else:
            # Обычные пользователи: дневная и вечерняя активность
            return {
                "activity_level": 0.7,
                "posts_per_hour": 0.5,
                "comments_per_hour": 1.2,
                "active_hours": [9, 10, 11, 12, 13, 18, 19, 20, 21, 22, 23],  # 白天+晚间
                "response_delay_min": 2,
                "response_delay_max": 20,
                "sentiment_bias": 0.0,
                "stance": "neutral",
                "influence_weight": 1.0
            }
    
