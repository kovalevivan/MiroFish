"""
Сервис обновления памяти графа Zep.
Динамически добавляет в граф действия агентов из симуляции.
"""

import os
import time
import threading
import json
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass
from datetime import datetime
from queue import Queue, Empty

from zep_cloud.client import Zep

from ..config import Config
from ..utils.logger import get_logger

logger = get_logger('mirofish.zep_graph_memory_updater')


@dataclass
class AgentActivity:
    """Запись об активности агента."""
    platform: str           # twitter / reddit
    agent_id: int
    agent_name: str
    action_type: str        # CREATE_POST, LIKE_POST, etc.
    action_args: Dict[str, Any]
    round_num: int
    timestamp: str
    
    def to_episode_text(self) -> str:
        """
        Преобразует действие в текстовое описание для Zep.
        Используется естественный язык без специальных префиксов симуляции.
        """
        # Для каждого типа действия выбираем свой шаблон описания
        action_descriptions = {
            "CREATE_POST": self._describe_create_post,
            "LIKE_POST": self._describe_like_post,
            "DISLIKE_POST": self._describe_dislike_post,
            "REPOST": self._describe_repost,
            "QUOTE_POST": self._describe_quote_post,
            "FOLLOW": self._describe_follow,
            "CREATE_COMMENT": self._describe_create_comment,
            "LIKE_COMMENT": self._describe_like_comment,
            "DISLIKE_COMMENT": self._describe_dislike_comment,
            "SEARCH_POSTS": self._describe_search,
            "SEARCH_USER": self._describe_search_user,
            "MUTE": self._describe_mute,
        }
        
        describe_func = action_descriptions.get(self.action_type, self._describe_generic)
        description = describe_func()
        
        # Возвращаем строку в формате "агент: описание действия"
        return f"{self.agent_name}: {description}"
    
    def _describe_create_post(self) -> str:
        content = self.action_args.get("content", "")
        if content:
            return f"опубликовал пост: «{content}»"
        return "опубликовал пост"
    
    def _describe_like_post(self) -> str:
        """Лайк поста с учетом текста и автора."""
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        
        if post_content and post_author:
            return f"поставил лайк посту автора {post_author}: «{post_content}»"
        elif post_content:
            return f"поставил лайк посту: «{post_content}»"
        elif post_author:
            return f"поставил лайк одному из постов автора {post_author}"
        return "поставил лайк посту"
    
    def _describe_dislike_post(self) -> str:
        """Дизлайк поста с учетом текста и автора."""
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        
        if post_content and post_author:
            return f"поставил дизлайк посту автора {post_author}: «{post_content}»"
        elif post_content:
            return f"поставил дизлайк посту: «{post_content}»"
        elif post_author:
            return f"поставил дизлайк одному из постов автора {post_author}"
        return "поставил дизлайк посту"
    
    def _describe_repost(self) -> str:
        """Репост поста с учетом исходного автора и текста."""
        original_content = self.action_args.get("original_content", "")
        original_author = self.action_args.get("original_author_name", "")
        
        if original_content and original_author:
            return f"сделал репост поста автора {original_author}: «{original_content}»"
        elif original_content:
            return f"сделал репост поста: «{original_content}»"
        elif original_author:
            return f"сделал репост одного из постов автора {original_author}"
        return "сделал репост поста"
    
    def _describe_quote_post(self) -> str:
        """Цитирование поста с комментарием."""
        original_content = self.action_args.get("original_content", "")
        original_author = self.action_args.get("original_author_name", "")
        quote_content = self.action_args.get("quote_content", "") or self.action_args.get("content", "")
        
        base = ""
        if original_content and original_author:
            base = f"процитировал пост автора {original_author} «{original_content}»"
        elif original_content:
            base = f"процитировал пост «{original_content}»"
        elif original_author:
            base = f"процитировал один из постов автора {original_author}"
        else:
            base = "процитировал пост"
        
        if quote_content:
            base += f", добавив комментарий: «{quote_content}»"
        return base
    
    def _describe_follow(self) -> str:
        """Подписка на пользователя."""
        target_user_name = self.action_args.get("target_user_name", "")
        
        if target_user_name:
            return f"подписался на пользователя «{target_user_name}»"
        return "подписался на пользователя"
    
    def _describe_create_comment(self) -> str:
        """Публикация комментария."""
        content = self.action_args.get("content", "")
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        
        if content:
            if post_content and post_author:
                return f"оставил комментарий под постом автора {post_author} «{post_content}»: «{content}»"
            elif post_content:
                return f"оставил комментарий под постом «{post_content}»: «{content}»"
            elif post_author:
                return f"оставил комментарий под постом автора {post_author}: «{content}»"
            return f"оставил комментарий: «{content}»"
        return "опубликовал комментарий"
    
    def _describe_like_comment(self) -> str:
        """Лайк комментария."""
        comment_content = self.action_args.get("comment_content", "")
        comment_author = self.action_args.get("comment_author_name", "")
        
        if comment_content and comment_author:
            return f"поставил лайк комментарию автора {comment_author}: «{comment_content}»"
        elif comment_content:
            return f"поставил лайк комментарию: «{comment_content}»"
        elif comment_author:
            return f"поставил лайк одному из комментариев автора {comment_author}"
        return "поставил лайк комментарию"
    
    def _describe_dislike_comment(self) -> str:
        """Дизлайк комментария."""
        comment_content = self.action_args.get("comment_content", "")
        comment_author = self.action_args.get("comment_author_name", "")
        
        if comment_content and comment_author:
            return f"поставил дизлайк комментарию автора {comment_author}: «{comment_content}»"
        elif comment_content:
            return f"поставил дизлайк комментарию: «{comment_content}»"
        elif comment_author:
            return f"поставил дизлайк одному из комментариев автора {comment_author}"
        return "поставил дизлайк комментарию"
    
    def _describe_search(self) -> str:
        """Поиск постов."""
        query = self.action_args.get("query", "") or self.action_args.get("keyword", "")
        return f"искал по запросу «{query}»" if query else "выполнил поиск"
    
    def _describe_search_user(self) -> str:
        """Поиск пользователей."""
        query = self.action_args.get("query", "") or self.action_args.get("username", "")
        return f"искал пользователя «{query}»" if query else "искал пользователя"
    
    def _describe_mute(self) -> str:
        """Блокировка пользователя."""
        target_user_name = self.action_args.get("target_user_name", "")
        
        if target_user_name:
            return f"заблокировал пользователя «{target_user_name}»"
        return "заблокировал пользователя"
    
    def _describe_generic(self) -> str:
        # Для неизвестных типов действий используем общий шаблон
        return f"выполнил действие {self.action_type}"


class ZepGraphMemoryUpdater:
    """
    Zep图谱记忆更新器
    
    监控模拟的actions日志文件，将新的agent活动实时更新到Zep图谱中。
    按平台分组，每累积BATCH_SIZE条活动后批量发送到Zep。
    
    所有有意义的行为都会被更新到Zep，action_args中会包含完整的上下文信息：
    - 点赞/踩的帖子原文
    - 转发/引用的帖子原文
    - 关注/屏蔽的用户名
    - 点赞/踩的评论原文
    """
    
    # 批量发送大小（每个平台累积多少条后发送）
    BATCH_SIZE = 5
    
    # 平台名称映射（用于控制台显示）
    PLATFORM_DISPLAY_NAMES = {
        'twitter': 'Twitter',
        'reddit': 'Reddit',
    }
    
    # 发送间隔（秒），避免请求过快
    SEND_INTERVAL = 0.5
    
    # 重试配置
    MAX_RETRIES = 3
    RETRY_DELAY = 2  # 秒
    
    def __init__(self, graph_id: str, api_key: Optional[str] = None):
        """Инициализирует updater для заданного graph_id."""
        self.graph_id = graph_id
        self.api_key = api_key or Config.ZEP_API_KEY
        
        if not self.api_key:
            raise ValueError("ZEP_API_KEY не настроен")
        
        self.client = Zep(api_key=self.api_key)
        
        # Очередь действий
        self._activity_queue: Queue = Queue()
        
        # Буферы действий по платформам
        self._platform_buffers: Dict[str, List[AgentActivity]] = {
            'twitter': [],
            'reddit': [],
        }
        self._buffer_lock = threading.Lock()
        
        # Флаги состояния
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
        
        # Статистика
        self._total_activities = 0
        self._total_sent = 0
        self._total_items_sent = 0
        self._failed_count = 0
        self._skipped_count = 0
        
        logger.info(f"ZepGraphMemoryUpdater инициализирован: graph_id={graph_id}, batch_size={self.BATCH_SIZE}")
    
    def _get_platform_display_name(self, platform: str) -> str:
        """Возвращает отображаемое имя платформы."""
        return self.PLATFORM_DISPLAY_NAMES.get(platform.lower(), platform)
    
    def start(self):
        """Запускает фоновый поток обработки."""
        if self._running:
            return
        
        self._running = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name=f"ZepMemoryUpdater-{self.graph_id[:8]}"
        )
        self._worker_thread.start()
        logger.info(f"ZepGraphMemoryUpdater запущен: graph_id={self.graph_id}")
    
    def stop(self):
        """Останавливает фоновый поток обработки."""
        self._running = False
        
        # Отправляем оставшиеся действия
        self._flush_remaining()
        
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=10)
        
        logger.info(f"ZepGraphMemoryUpdater остановлен: graph_id={self.graph_id}, "
                   f"total_activities={self._total_activities}, "
                   f"batches_sent={self._total_sent}, "
                   f"items_sent={self._total_items_sent}, "
                   f"failed={self._failed_count}, "
                   f"skipped={self._skipped_count}")
    
    def add_activity(self, activity: AgentActivity):
        """Добавляет действие агента в очередь обновления Zep."""
        # Пропускаем действия без эффекта
        if activity.action_type == "DO_NOTHING":
            self._skipped_count += 1
            return
        
        self._activity_queue.put(activity)
        self._total_activities += 1
        logger.debug(f"Действие добавлено в очередь Zep: {activity.agent_name} - {activity.action_type}")
    
    def add_activity_from_dict(self, data: Dict[str, Any], platform: str):
        """Создает и добавляет действие из словаря."""
        # Пропускаем записи о событиях
        if "event_type" in data:
            return
        
        activity = AgentActivity(
            platform=platform,
            agent_id=data.get("agent_id", 0),
            agent_name=data.get("agent_name", ""),
            action_type=data.get("action_type", ""),
            action_args=data.get("action_args", {}),
            round_num=data.get("round", 0),
            timestamp=data.get("timestamp", datetime.now().isoformat()),
        )
        
        self.add_activity(activity)
    
    def _worker_loop(self):
        """Фоновый цикл: отправляет действия в Zep пакетами по платформам."""
        while self._running or not self._activity_queue.empty():
            try:
                # Пытаемся забрать действие из очереди
                try:
                    activity = self._activity_queue.get(timeout=1)
                    
                    # Перекладываем действие в буфер платформы
                    platform = activity.platform.lower()
                    with self._buffer_lock:
                        if platform not in self._platform_buffers:
                            self._platform_buffers[platform] = []
                        self._platform_buffers[platform].append(activity)
                        
                        # Если буфер достиг размера пакета, отправляем его
                        if len(self._platform_buffers[platform]) >= self.BATCH_SIZE:
                            batch = self._platform_buffers[platform][:self.BATCH_SIZE]
                            self._platform_buffers[platform] = self._platform_buffers[platform][self.BATCH_SIZE:]
                            # Отправляем после выхода из критической секции
                            self._send_batch_activities(batch, platform)
                            # Небольшая пауза между отправками
                            time.sleep(self.SEND_INTERVAL)
                    
                except Empty:
                    pass
                    
            except Exception as e:
                logger.error(f"Ошибка фонового цикла updater: {e}")
                time.sleep(1)
    
    def _send_batch_activities(self, activities: List[AgentActivity], platform: str):
        """Пакетно отправляет действия в граф Zep как единый текст."""
        if not activities:
            return
        
        # Объединяем действия в один эпизод
        episode_texts = [activity.to_episode_text() for activity in activities]
        combined_text = "\n".join(episode_texts)
        
        # Отправка с повторными попытками
        for attempt in range(self.MAX_RETRIES):
            try:
                self.client.graph.add(
                    graph_id=self.graph_id,
                    type="text",
                    data=combined_text
                )
                
                self._total_sent += 1
                self._total_items_sent += len(activities)
                display_name = self._get_platform_display_name(platform)
                logger.info(f"Успешно отправлен пакет из {len(activities)} действий платформы {display_name} в граф {self.graph_id}")
                logger.debug(f"Предпросмотр отправленного текста: {combined_text[:200]}...")
                return
                
            except Exception as e:
                if attempt < self.MAX_RETRIES - 1:
                    logger.warning(f"Не удалось отправить пакет в Zep (попытка {attempt + 1}/{self.MAX_RETRIES}): {e}")
                    time.sleep(self.RETRY_DELAY * (attempt + 1))
                else:
                    logger.error(f"Не удалось отправить пакет в Zep после {self.MAX_RETRIES} попыток: {e}")
                    self._failed_count += 1
    
    def _flush_remaining(self):
        """Отправляет все оставшиеся действия из очереди и буферов."""
        # Сначала переносим остаток очереди в буферы
        while not self._activity_queue.empty():
            try:
                activity = self._activity_queue.get_nowait()
                platform = activity.platform.lower()
                with self._buffer_lock:
                    if platform not in self._platform_buffers:
                        self._platform_buffers[platform] = []
                    self._platform_buffers[platform].append(activity)
            except Empty:
                break
        
        # Затем отправляем оставшиеся буферы даже если они неполные
        with self._buffer_lock:
            for platform, buffer in self._platform_buffers.items():
                if buffer:
                    display_name = self._get_platform_display_name(platform)
                    logger.info(f"Отправляю остаток буфера платформы {display_name}: {len(buffer)} действий")
                    self._send_batch_activities(buffer, platform)
            # Очищаем буферы
            for platform in self._platform_buffers:
                self._platform_buffers[platform] = []
    
    def get_stats(self) -> Dict[str, Any]:
        """Возвращает статистику updater-а."""
        with self._buffer_lock:
            buffer_sizes = {p: len(b) for p, b in self._platform_buffers.items()}
        
        return {
            "graph_id": self.graph_id,
            "batch_size": self.BATCH_SIZE,
            "total_activities": self._total_activities,
            "batches_sent": self._total_sent,
            "items_sent": self._total_items_sent,
            "failed_count": self._failed_count,
            "skipped_count": self._skipped_count,
            "queue_size": self._activity_queue.qsize(),
            "buffer_sizes": buffer_sizes,
            "running": self._running,
        }


class ZepGraphMemoryManager:
    """Менеджер нескольких updater-ов памяти графа Zep."""
    
    _updaters: Dict[str, ZepGraphMemoryUpdater] = {}
    _lock = threading.Lock()
    
    @classmethod
    def create_updater(cls, simulation_id: str, graph_id: str) -> ZepGraphMemoryUpdater:
        """Создает updater для конкретной симуляции."""
        with cls._lock:
            # Если updater уже существует, останавливаем старый
            if simulation_id in cls._updaters:
                cls._updaters[simulation_id].stop()
            
            updater = ZepGraphMemoryUpdater(graph_id)
            updater.start()
            cls._updaters[simulation_id] = updater
            
            logger.info(f"Создан updater памяти графа: simulation_id={simulation_id}, graph_id={graph_id}")
            return updater
    
    @classmethod
    def get_updater(cls, simulation_id: str) -> Optional[ZepGraphMemoryUpdater]:
        """Возвращает updater для симуляции."""
        return cls._updaters.get(simulation_id)
    
    @classmethod
    def stop_updater(cls, simulation_id: str):
        """Останавливает и удаляет updater симуляции."""
        with cls._lock:
            if simulation_id in cls._updaters:
                cls._updaters[simulation_id].stop()
                del cls._updaters[simulation_id]
                logger.info(f"Updater памяти графа остановлен: simulation_id={simulation_id}")
    
    # Защита от повторного вызова stop_all
    _stop_all_done = False
    
    @classmethod
    def stop_all(cls):
        """Останавливает все updater-ы."""
        # Предотвращаем повторный вызов
        if cls._stop_all_done:
            return
        cls._stop_all_done = True
        
        with cls._lock:
            if cls._updaters:
                for simulation_id, updater in list(cls._updaters.items()):
                    try:
                        updater.stop()
                    except Exception as e:
                        logger.error(f"Не удалось остановить updater: simulation_id={simulation_id}, error={e}")
                cls._updaters.clear()
            logger.info("Все updater-ы памяти графа остановлены")
    
    @classmethod
    def get_all_stats(cls) -> Dict[str, Dict[str, Any]]:
        """Возвращает статистику всех updater-ов."""
        return {
            sim_id: updater.get_stats() 
            for sim_id, updater in cls._updaters.items()
        }
