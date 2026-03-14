"""
Сервис Report Agent.

Использует LangChain и Zep для генерации аналитического отчета в режиме ReACT.
"""

import os
import json
import time
import re
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..config import Config
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger
from .zep_tools import (
    ZepToolsService, 
    SearchResult, 
    InsightForgeResult, 
    PanoramaResult,
    InterviewResult
)

logger = get_logger('mirofish.report_agent')


def localize_provider_error(error: Any) -> str:
    """Приводит сырые ошибки внешних провайдеров к русскому виду."""
    text = str(error)
    replacements = [
        ("Agent not active", "Агент не активен"),
        ("permission_error", "ошибка доступа"),
        ("agent_not_active", "агент не активен"),
        ("Error code:", "Код ошибки:"),
        ("'error':", "'ошибка':"),
        ("'message':", "'сообщение':"),
        ("'type':", "'тип':"),
        ("'param':", "'параметр':"),
        ("'code':", "'код':"),
        (": None", ": нет"),
        (": null", ": нет"),
    ]

    for source, target in replacements:
        text = text.replace(source, target)

    return text


def contains_han_text(text: Optional[str]) -> bool:
    """Проверяет, содержит ли текст китайские иероглифы."""
    return bool(text and re.search(r'[\u3400-\u9fff]', text))


class ReportLogger:
    """Подробный логгер работы Report Agent."""
    
    def __init__(self, report_id: str):
        """Инициализирует логгер для конкретного отчета."""
        self.report_id = report_id
        self.log_file_path = os.path.join(
            Config.UPLOAD_FOLDER, 'reports', report_id, 'agent_log.jsonl'
        )
        self.start_time = datetime.now()
        self._ensure_log_file()
    
    def _ensure_log_file(self):
        """Гарантирует существование каталога логов."""
        log_dir = os.path.dirname(self.log_file_path)
        os.makedirs(log_dir, exist_ok=True)
    
    def _get_elapsed_time(self) -> float:
        """Возвращает прошедшее время в секундах."""
        return (datetime.now() - self.start_time).total_seconds()
    
    def log(
        self, 
        action: str, 
        stage: str,
        details: Dict[str, Any],
        section_title: str = None,
        section_index: int = None
    ):
        """Записывает одну structured-log запись."""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "elapsed_seconds": round(self._get_elapsed_time(), 2),
            "report_id": self.report_id,
            "action": action,
            "stage": stage,
            "section_title": section_title,
            "section_index": section_index,
            "details": details
        }
        
        # Дописываем запись в JSONL
        with open(self.log_file_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
    
    def log_start(self, simulation_id: str, graph_id: str, simulation_requirement: str):
        """Фиксирует старт генерации отчета."""
        self.log(
            action="report_start",
            stage="pending",
            details={
                "simulation_id": simulation_id,
                "graph_id": graph_id,
                "simulation_requirement": simulation_requirement,
                "message": "Запущена задача генерации отчета"
            }
        )
    
    def log_planning_start(self):
        """Фиксирует старт планирования структуры."""
        self.log(
            action="planning_start",
            stage="planning",
            details={"message": "Начато планирование структуры отчета"}
        )
    
    def log_planning_context(self, context: Dict[str, Any]):
        """Фиксирует контекст, собранный на этапе планирования."""
        self.log(
            action="planning_context",
            stage="planning",
            details={
                "message": "Получен контекст симуляции",
                "context": context
            }
        )
    
    def log_planning_complete(self, outline_dict: Dict[str, Any]):
        """Фиксирует завершение планирования."""
        self.log(
            action="planning_complete",
            stage="planning",
            details={
                "message": "Планирование структуры завершено",
                "outline": outline_dict
            }
        )
    
    def log_section_start(self, section_title: str, section_index: int):
        """Фиксирует старт генерации раздела."""
        self.log(
            action="section_start",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={"message": f"Начата генерация раздела: {section_title}"}
        )
    
    def log_react_thought(self, section_title: str, section_index: int, iteration: int, thought: str):
        """Фиксирует очередной шаг ReACT-рассуждения."""
        self.log(
            action="react_thought",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "thought": thought,
                "message": f"ReACT: итерация {iteration}"
            }
        )
    
    def log_tool_call(
        self, 
        section_title: str, 
        section_index: int,
        tool_name: str, 
        parameters: Dict[str, Any],
        iteration: int
    ):
        """Фиксирует вызов инструмента."""
        self.log(
            action="tool_call",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "tool_name": tool_name,
                "parameters": parameters,
                "message": f"Вызван инструмент: {tool_name}"
            }
        )
    
    def log_tool_result(
        self,
        section_title: str,
        section_index: int,
        tool_name: str,
        result: str,
        iteration: int
    ):
        """Фиксирует полный результат вызова инструмента."""
        self.log(
            action="tool_result",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "tool_name": tool_name,
                "result": result,
                "result_length": len(result),
                "message": f"Инструмент {tool_name} вернул результат"
            }
        )
    
    def log_llm_response(
        self,
        section_title: str,
        section_index: int,
        response: str,
        iteration: int,
        has_tool_calls: bool,
        has_final_answer: bool
    ):
        """Фиксирует полный ответ LLM."""
        self.log(
            action="llm_response",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "response": response,
                "response_length": len(response),
                "has_tool_calls": has_tool_calls,
                "has_final_answer": has_final_answer,
                "message": f"Ответ LLM (вызовы инструментов: {has_tool_calls}, финальный ответ: {has_final_answer})"
            }
        )
    
    def log_section_content(
        self,
        section_title: str,
        section_index: int,
        content: str,
        tool_calls_count: int
    ):
        """Фиксирует содержимое раздела после генерации."""
        self.log(
            action="section_content",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "content": content,
                "content_length": len(content),
                "tool_calls_count": tool_calls_count,
                "message": f"Содержимое раздела {section_title} сгенерировано"
            }
        )
    
    def log_section_full_complete(
        self,
        section_title: str,
        section_index: int,
        full_content: str
    ):
        """
        记录章节生成完成

        前端应监听此日志来判断一个章节是否真正完成，并获取完整内容
        """
        self.log(
            action="section_complete",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "content": full_content,
                "content_length": len(full_content),
                "message": f"Раздел {section_title} полностью завершен"
            }
        )
    
    def log_report_complete(self, total_sections: int, total_time_seconds: float):
        """Фиксирует успешное завершение генерации отчета."""
        self.log(
            action="report_complete",
            stage="completed",
            details={
                "total_sections": total_sections,
                "total_time_seconds": round(total_time_seconds, 2),
                "message": "Генерация отчета завершена"
            }
        )
    
    def log_error(self, error_message: str, stage: str, section_title: str = None):
        """Фиксирует ошибку генерации."""
        self.log(
            action="error",
            stage=stage,
            section_title=section_title,
            section_index=None,
            details={
                "error": error_message,
                "message": f"Произошла ошибка: {error_message}"
            }
        )


class ReportConsoleLogger:
    """
    Report Agent 控制台日志记录器
    
    将控制台风格的日志（INFO、WARNING等）写入报告文件夹中的 console_log.txt 文件。
    这些日志与 agent_log.jsonl 不同，是纯文本格式的控制台输出。
    """
    
    def __init__(self, report_id: str):
        """
        初始化控制台日志记录器
        
        Args:
            report_id: 报告ID，用于确定日志文件路径
        """
        self.report_id = report_id
        self.log_file_path = os.path.join(
            Config.UPLOAD_FOLDER, 'reports', report_id, 'console_log.txt'
        )
        self._ensure_log_file()
        self._file_handler = None
        self._setup_file_handler()
    
    def _ensure_log_file(self):
        """Гарантирует существование каталога логов."""
        log_dir = os.path.dirname(self.log_file_path)
        os.makedirs(log_dir, exist_ok=True)
    
    def _setup_file_handler(self):
        """Подключает файловый handler к logger."""
        import logging
        
        # 创建文件处理器
        self._file_handler = logging.FileHandler(
            self.log_file_path,
            mode='a',
            encoding='utf-8'
        )
        self._file_handler.setLevel(logging.INFO)
        
        # 使用与控制台相同的简洁格式
        formatter = logging.Formatter(
            '[%(asctime)s] %(levelname)s: %(message)s',
            datefmt='%H:%M:%S'
        )
        self._file_handler.setFormatter(formatter)
        
        # 添加到 report_agent 相关的 logger
        loggers_to_attach = [
            'mirofish.report_agent',
            'mirofish.zep_tools',
        ]
        
        for logger_name in loggers_to_attach:
            target_logger = logging.getLogger(logger_name)
            # 避免重复添加
            if self._file_handler not in target_logger.handlers:
                target_logger.addHandler(self._file_handler)
    
    def close(self):
        """Закрывает и удаляет файловый handler."""
        import logging
        
        if self._file_handler:
            loggers_to_detach = [
                'mirofish.report_agent',
                'mirofish.zep_tools',
            ]
            
            for logger_name in loggers_to_detach:
                target_logger = logging.getLogger(logger_name)
                if self._file_handler in target_logger.handlers:
                    target_logger.removeHandler(self._file_handler)
            
            self._file_handler.close()
            self._file_handler = None
    
    def __del__(self):
        """При уничтожении объекта гарантированно закрывает handler."""
        self.close()


class ReportStatus(str, Enum):
    """Статус отчета."""
    PENDING = "pending"
    PLANNING = "planning"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ReportSection:
    """Раздел отчета."""
    title: str
    content: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "content": self.content
        }

    def to_markdown(self, level: int = 2) -> str:
        """Преобразует раздел в Markdown."""
        md = f"{'#' * level} {self.title}\n\n"
        if self.content:
            md += f"{self.content}\n\n"
        return md


@dataclass
class ReportOutline:
    """Структура отчета."""
    title: str
    summary: str
    sections: List[ReportSection]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "sections": [s.to_dict() for s in self.sections]
        }
    
    def to_markdown(self) -> str:
        """Преобразует структуру в Markdown."""
        md = f"# {self.title}\n\n"
        md += f"> {self.summary}\n\n"
        for section in self.sections:
            md += section.to_markdown()
        return md


@dataclass
class Report:
    """Полный отчет."""
    report_id: str
    simulation_id: str
    graph_id: str
    simulation_requirement: str
    status: ReportStatus
    outline: Optional[ReportOutline] = None
    markdown_content: str = ""
    created_at: str = ""
    completed_at: str = ""
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "simulation_id": self.simulation_id,
            "graph_id": self.graph_id,
            "simulation_requirement": self.simulation_requirement,
            "status": self.status.value,
            "outline": self.outline.to_dict() if self.outline else None,
            "markdown_content": self.markdown_content,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "error": self.error
        }


# ═══════════════════════════════════════════════════════════════
# Prompt 模板常量
# ═══════════════════════════════════════════════════════════════

# ── 工具描述 ──

TOOL_DESC_INSIGHT_FORGE = """\
【Глубокий анализ】
Сильный инструмент для углубленного разбора темы. Он:
1. Делит вопрос на несколько подвопросов.
2. Ищет релевантную информацию в графе симуляции с разных сторон.
3. Объединяет результаты семантического поиска, анализа сущностей и цепочек связей.
4. Возвращает насыщенный материал для доказательного раздела отчета.

【Когда использовать】
- Нужно глубоко разобрать тему.
- Нужно увидеть несколько сторон одного события.
- Нужно собрать плотную фактическую базу для раздела.

【Что возвращает】
- релевантные факты, которые можно цитировать;
- выводы по ключевым сущностям;
- цепочки связей и причинно-следственные линии."""

TOOL_DESC_PANORAMA_SEARCH = """\
【Панорамный поиск】
Инструмент для обзора общей картины и траектории развития событий. Он:
1. Собирает все связанные узлы и связи.
2. Разделяет актуальные факты и исторические/истекшие факты.
3. Помогает увидеть, как менялась ситуация и как эволюционировали реакции.

【Когда использовать】
- Нужно понять полную траекторию развития событий.
- Нужно сравнить разные этапы и волны реакции.
- Нужно получить широкую картину по сущностям и связям.

【Что возвращает】
- текущие актуальные факты;
- исторические и устаревшие факты;
- список задействованных сущностей."""

TOOL_DESC_QUICK_SEARCH = """\
【Быстрый поиск】
Легкий инструмент для коротких и прямых информационных запросов.

【Когда использовать】
- Нужно быстро найти конкретный факт.
- Нужно проверить одно утверждение.
- Нужен адресный поиск без глубокого обхода.

【Что возвращает】
- список фактов, наиболее релевантных запросу."""

TOOL_DESC_INTERVIEW_AGENTS = """\
【Интервью с агентами симуляции】
Этот инструмент обращается к реальному API интервью среды OASIS и получает ответы от уже работающих агентов.
Это не выдуманное LLM-интервью, а извлечение реальных ответов из симуляции. По умолчанию опрашиваются обе площадки мира.

【Как работает】
1. Читает профили агентов.
2. Выбирает наиболее релевантных участников под тему интервью.
3. Формирует вопросы.
4. Вызывает `/api/simulation/interview/batch` и получает реальные ответы.
5. Собирает многоракурсный материал для анализа.

【Когда использовать】
- Нужно понять взгляды разных ролей на событие.
- Нужно собрать позиции, эмоции и аргументы разных групп.
- Нужен более живой материал с цитатами и фрагментами интервью.

【Что возвращает】
- информацию о выбранных агентах;
- ответы агентов на разных площадках;
- ключевые цитаты;
- краткое сравнение позиций.

【Важно】
Инструмент работает только когда среда OASIS запущена."""

# ── 大纲规划 prompt ──

PLAN_SYSTEM_PROMPT = """\
Ты эксперт по подготовке «прогнозных отчетов о будущем» и смотришь на мир симуляции с позиции наблюдателя, который видит действия, высказывания и взаимодействия всех агентов.

【Ключевая идея】
Мы построили мир симуляции и ввели в него конкретное условие, описанное в simulation requirement. Эволюция этого мира является прогнозом того, что может произойти в будущем. Ты анализируешь не «экспериментальные данные», а «репетицию будущего».

【Твоя задача】
Подготовить структуру прогнозного отчета, который отвечает на вопросы:
1. Что произошло в будущем при заданных условиях?
2. Как реагировали и действовали разные группы агентов?
3. Какие тренды, риски и сигналы выявила симуляция?

【Позиционирование отчета】
- ✅ Это именно прогнозный отчет по результатам симуляции: «если условия такие, то что будет дальше».
- ✅ Фокус на прогнозе: ход событий, реакции групп, возникающие эффекты, потенциальные риски.
- ✅ Поведение агентов внутри симуляции трактуется как модель будущего поведения людей и организаций.
- ❌ Это не обзор текущего состояния реального мира.
- ❌ Это не общий пересказ информационного фона.

【Ограничения по структуре】
- От 2 до 5 разделов.
- Без подразделов: каждый раздел должен быть цельным смысловым блоком.
- Структура должна быть лаконичной и сосредоточенной на главных прогнозных выводах.

Верни JSON с такой структурой:
{
    "title": "название отчета",
    "summary": "краткое резюме отчета в одном предложении",
    "sections": [
        {
            "title": "название раздела",
            "description": "краткое описание содержания раздела"
        }
    ]
}

Важно:
- в массиве sections должно быть не меньше 2 и не больше 5 элементов;
- title, summary и все section.title должны быть написаны только на русском языке;
- нельзя использовать китайский язык и китайские иероглифы."""

PLAN_USER_PROMPT_TEMPLATE = """\
【Сценарий прогноза】
Условие, внедренное в мир симуляции: {simulation_requirement}

【Масштаб мира симуляции】
- Число узлов: {total_nodes}
- Число связей: {total_edges}
- Распределение типов сущностей: {entity_types}
- Число активных агентов: {total_entities}

【Примеры зафиксированных прогнозных фактов】
{related_facts_json}

Посмотри на эту репетицию будущего и определи:
1. В каком состоянии оказался мир при заданных условиях?
2. Как действовали разные группы агентов?
3. Какие значимые тренды следует вынести в отчет?

Сконструируй наиболее подходящую структуру отчета.

Напоминание: от 2 до 5 разделов, содержание должно быть компактным и сфокусированным на главных прогнозных выводах."""

# ── 章节生成 prompt ──

SECTION_SYSTEM_PROMPT_TEMPLATE = """\
Ты пишешь один раздел «прогнозного отчета о будущем».

Название отчета: {report_title}
Резюме отчета: {report_summary}
Сценарий прогноза: {simulation_requirement}

Текущий раздел: {section_title}

═══════════════════════════════════════════════════════════════
【Ключевая идея】
═══════════════════════════════════════════════════════════════

Мир симуляции является репетицией будущего. Мы ввели в него заданные условия,
а поведение и взаимодействия агентов выступают моделью будущего поведения людей и организаций.

Твоя задача:
- показать, что именно произошло в будущем при заданных условиях;
- объяснить, как реагировали и действовали разные группы агентов;
- выявить важные будущие тренды, риски и возможности.

❌ Не превращай текст в анализ текущей реальности.
✅ Фокусируйся на том, что произойдет дальше: результат симуляции и есть прогноз.

═══════════════════════════════════════════════════════════════
【Главные правила, обязательные к соблюдению】
═══════════════════════════════════════════════════════════════

1. 【Обязательно наблюдай мир через инструменты】
   - Ты смотришь на репетицию будущего.
   - Все содержание должно опираться только на события симуляции и слова/действия агентов.
   - Нельзя использовать собственные знания вне результатов симуляции.
   - Для каждого раздела нужно вызвать инструменты минимум 3 раза и максимум 5 раз.

2. 【Обязательно цитируй исходные высказывания агентов】
   - Реплики и действия агентов являются основным доказательством прогноза.
   - Используй цитаты в формате:
     > "Представители группы, вероятно, скажут: ..."
   - Эти цитаты должны быть опорой для выводов раздела.

3. 【Язык отчета должен быть единым】
   - Инструменты могут вернуть текст на английском или в смешанном виде.
   - Отчет должен быть полностью написан на русском языке.
   - Если ты цитируешь англоязычный или смешанный текст, переводи его на грамотный русский, сохраняя смысл.
   - Это относится и к основному тексту, и к цитатам.
   - Категорически запрещено использовать китайский язык, китайские иероглифы и смешанные русско-китайские фрагменты.

4. 【Точно следуй прогнозным данным】
   - Раздел должен отражать только то, что действительно произошло в симуляции.
   - Не добавляй факты, которых не было в результатах.
   - Если данных не хватает, прямо укажи на это.

═══════════════════════════════════════════════════════════════
【⚠️ Формат раздела】
═══════════════════════════════════════════════════════════════

【Один раздел = один минимальный смысловой блок】
- Один раздел должен быть самодостаточным текстовым блоком.
- ❌ Нельзя использовать markdown-заголовки внутри раздела (`#`, `##`, `###`, `####` и т.д.).
- ❌ Нельзя дублировать название раздела в начале текста.
- ✅ Название раздела будет добавлено системой автоматически.
- ✅ Для структуры используй **жирный текст**, абзацы, цитаты и списки, но не заголовки.

【Корректный пример】
```
В этом разделе рассматривается динамика распространения обсуждения. На основе симуляции видно, что...

**Этап первичного всплеска**

Первая волна обсуждения запускает распространение темы и задает тон дискуссии:

> "На стартовой стадии основная доля первых публикаций пришлась на одну площадку..."

**Этап усиления эмоций**

Следующая платформа усиливает эмоциональный отклик и расширяет охват:

- высокий визуальный эффект
- сильный эмоциональный отклик
```

【Некорректный пример】
```
## Резюме          ← ошибка, не добавляй заголовки
### Первый этап   ← ошибка, не используй подзаголовки
#### 1.1 Детали   ← ошибка, не дроби раздел заголовками

В этом разделе анализируется...
```

═══════════════════════════════════════════════════════════════
【Доступные инструменты поиска】 (3-5 вызовов на раздел)
═══════════════════════════════════════════════════════════════

{tools_description}

【Рекомендации по использованию инструментов】
- `insight_forge`: глубокий анализ, разложение вопроса на подвопросы, поиск фактов и связей.
- `panorama_search`: панорамный обзор, временная линия, эволюция событий.
- `quick_search`: быстрая проверка конкретного факта.
- `interview_agents`: интервью с агентами симуляции и получение реакций от первого лица.

═══════════════════════════════════════════════════════════════
【Рабочий процесс】
═══════════════════════════════════════════════════════════════

В каждом ответе можно сделать только одно из двух:

Вариант A: вызвать инструмент.
Сначала изложи мысль, затем вызови один инструмент в формате:
<tool_call>
{{"name": "имя_инструмента", "parameters": {{"имя_параметра": "значение"}}}}
</tool_call>
Система сама выполнит инструмент и вернет результат. Нельзя выдумывать результат самостоятельно.

Вариант B: вывести финальный текст раздела.
Когда информации уже достаточно, начни ответ с `Final Answer:` и напиши содержимое раздела.

⚠️ Строго запрещено:
- в одном ответе одновременно давать вызов инструмента и `Final Answer`;
- придумывать `Observation` или любой результат инструмента;
- вызывать больше одного инструмента за ответ.

═══════════════════════════════════════════════════════════════
【Требования к содержанию раздела】
═══════════════════════════════════════════════════════════════

1. Текст должен основываться только на данных, полученных через инструменты.
2. Активно используй цитаты и конкретные наблюдения из симуляции.
3. Используй markdown без заголовков:
   - **жирный текст** для смысловых акцентов;
   - списки для тезисов;
   - пустые строки между абзацами;
   - ❌ никаких markdown-заголовков.
4. 【Цитаты должны быть отдельными абзацами】
   Правильно:
   ```
   Реакция института была воспринята как слабая.

   > "Ответ выглядел медленным и негибким на фоне скорости соцсетей."

   Это усилило недоверие аудитории.
   ```
   Неправильно:
   ```
   Реакция института была слабой. > "Ответ выглядел..." Это усилило недоверие.
   ```
5. Сохраняй связность с уже написанными разделами.
6. Внимательно читай предыдущие разделы и не повторяй одни и те же факты.
7. Еще раз: не добавляй заголовки, используй **жирный текст** вместо них."""

SECTION_USER_PROMPT_TEMPLATE = """\
Уже готовые разделы (внимательно прочитай и не повторяйся):
{previous_content}

═══════════════════════════════════════════════════════════════
【Текущая задача】 написать раздел: {section_title}
═══════════════════════════════════════════════════════════════

【Важные напоминания】
1. Не повторяй содержание уже готовых разделов.
2. Перед написанием обязательно сначала вызови инструменты и получи данные.
3. Комбинируй разные инструменты, не ограничивайся одним.
4. Не используй собственные знания вне результатов поиска.

【⚠️ Формат обязателен】
- ❌ Не используй заголовки (`#`, `##`, `###`, `####`).
- ❌ Не начинай текст с "{section_title}".
- ✅ Название раздела будет добавлено системой.
- ✅ Сразу пиши основной текст, используя **жирный текст** вместо подзаголовков.

Начинай так:
1. Сначала подумай, какая информация нужна для раздела.
2. Затем вызови инструмент и собери данные.
3. Когда информации достаточно, выдай `Final Answer:` с чистым текстом раздела."""

# ── ReACT 循环内消息模板 ──

REACT_OBSERVATION_TEMPLATE = """\
Observation (результат поиска):

═══ Ответ инструмента {tool_name} ═══
{result}

═══════════════════════════════════════════════════════════════
Инструмент вызван {tool_calls_count}/{max_tool_calls} раз (уже использованы: {used_tools_str}){unused_hint}
- Если данных достаточно: выдай `Final Answer:` и напиши раздел, опираясь на материал выше.
- Если данных недостаточно: вызови еще один инструмент.
═══════════════════════════════════════════════════════════════"""

REACT_INSUFFICIENT_TOOLS_MSG = (
    "Недостаточно инструментальных вызовов: сейчас {tool_calls_count}, минимум нужен {min_tool_calls}. "
    "Сначала получи больше данных через инструменты, затем выводи Final Answer. {unused_hint}"
)

REACT_INSUFFICIENT_TOOLS_MSG_ALT = (
    "Сейчас выполнено только {tool_calls_count} вызовов инструментов, минимум нужен {min_tool_calls}. "
    "Вызови инструмент и добери данные. {unused_hint}"
)

REACT_TOOL_LIMIT_MSG = (
    "Достигнут лимит вызовов инструментов ({tool_calls_count}/{max_tool_calls}); больше вызывать нельзя. "
    'Немедленно выдай итоговый текст, начиная с "Final Answer:".'
)

REACT_UNUSED_TOOLS_HINT = "\nЕще не использованы инструменты: {unused_list}. Лучше комбинировать разные инструменты для многогранной картины."

REACT_FORCE_FINAL_MSG = 'Достигнут лимит вызовов инструментов. Сразу выдай "Final Answer:" и сформируй содержимое раздела.'

# ── Chat prompt ──

CHAT_SYSTEM_PROMPT_TEMPLATE = """\
Ты лаконичный и эффективный помощник по прогнозной симуляции.

【Контекст】
Условие прогноза: {simulation_requirement}

【Уже подготовленный отчет】
{report_content}

【Правила】
1. В первую очередь опирайся на содержимое отчета выше.
2. Отвечай прямо и коротко, без лишних рассуждений.
3. Используй инструменты только если отчета недостаточно для ответа.
4. Ответ должен быть ясным, структурным и компактным.

【Доступные инструменты】 (используй только при необходимости, максимум 1-2 вызова)
{tools_description}

【Формат вызова инструмента】
<tool_call>
{{"name": "имя_инструмента", "parameters": {{"имя_параметра": "значение"}}}}
</tool_call>

【Стиль ответа】
- коротко и по существу;
- при необходимости используй цитаты через `>`;
- сначала вывод, затем краткое пояснение."""

CHAT_OBSERVATION_SUFFIX = "\n\nОтветь кратко и по существу."


# ═══════════════════════════════════════════════════════════════
# ReportAgent 主类
# ═══════════════════════════════════════════════════════════════


class ReportAgent:
    """
    Report Agent - 模拟报告生成Agent

    采用ReACT（Reasoning + Acting）模式：
    1. 规划阶段：分析模拟需求，规划报告目录结构
    2. 生成阶段：逐章节生成内容，每章节可多次调用工具获取信息
    3. 反思阶段：检查内容完整性和准确性
    """
    
    # 最大工具调用次数（每个章节）
    MAX_TOOL_CALLS_PER_SECTION = 5
    
    # 最大反思轮数
    MAX_REFLECTION_ROUNDS = 3
    
    # 对话中的最大工具调用次数
    MAX_TOOL_CALLS_PER_CHAT = 2
    
    def __init__(
        self, 
        graph_id: str,
        simulation_id: str,
        simulation_requirement: str,
        llm_client: Optional[LLMClient] = None,
        zep_tools: Optional[ZepToolsService] = None
    ):
        """
        初始化Report Agent
        
        Args:
            graph_id: 图谱ID
            simulation_id: 模拟ID
            simulation_requirement: 模拟需求描述
            llm_client: LLM客户端（可选）
            zep_tools: Zep工具服务（可选）
        """
        self.graph_id = graph_id
        self.simulation_id = simulation_id
        self.simulation_requirement = simulation_requirement
        
        self.llm = llm_client or LLMClient()
        self.zep_tools = zep_tools or ZepToolsService()
        
        # 工具定义
        self.tools = self._define_tools()
        
        # 日志记录器（在 generate_report 中初始化）
        self.report_logger: Optional[ReportLogger] = None
        # 控制台日志记录器（在 generate_report 中初始化）
        self.console_logger: Optional[ReportConsoleLogger] = None
        
        logger.info(f"ReportAgent инициализирован: graph_id={graph_id}, simulation_id={simulation_id}")

    def _ensure_russian_text(self, text: str, content_label: str) -> str:
        """Принудительно переводит китайский текст на русский, сохраняя структуру."""
        if not text or not contains_han_text(text):
            return text

        logger.warning(f"{content_label} содержит китайский текст, выполняю перевод на русский")
        try:
            translated = self.llm.chat(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Ты профессиональный редактор. Переведи входной текст на грамотный русский язык. "
                            "Сохрани markdown-структуру, абзацы, списки, цитаты, имена собственные и смысл. "
                            "Не добавляй пояснений, комментариев и новых фактов. Верни только переведенный текст."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Переведи на русский следующий фрагмент ({content_label}):\n\n{text}",
                    },
                ],
                temperature=0.1,
                max_tokens=4096,
            )
            translated = (translated or "").strip()
            return translated or text
        except Exception as e:
            logger.warning(f"Не удалось перевести {content_label} на русский: {localize_provider_error(e)}")
            return text

    def _ensure_russian_outline(self, outline: ReportOutline) -> ReportOutline:
        """Приводит заголовок, резюме и названия разделов к русскому языку."""
        return ReportOutline(
            title=self._ensure_russian_text(outline.title, "название отчета"),
            summary=self._ensure_russian_text(outline.summary, "резюме отчета"),
            sections=[
                ReportSection(
                    title=self._ensure_russian_text(section.title, f"название раздела {idx + 1}"),
                    content=section.content,
                )
                for idx, section in enumerate(outline.sections)
            ],
        )
    
    def _define_tools(self) -> Dict[str, Dict[str, Any]]:
        """Описывает доступные инструменты."""
        return {
            "insight_forge": {
                "name": "insight_forge",
                "description": TOOL_DESC_INSIGHT_FORGE,
                "parameters": {
                    "query": "Вопрос или тема для углубленного анализа",
                    "report_context": "Контекст текущего раздела отчета (необязательно, помогает точнее сформировать подвопросы)"
                }
            },
            "panorama_search": {
                "name": "panorama_search",
                "description": TOOL_DESC_PANORAMA_SEARCH,
                "parameters": {
                    "query": "Поисковый запрос для сортировки по релевантности",
                    "include_expired": "Нужно ли включать исторические и истекшие факты (по умолчанию True)"
                }
            },
            "quick_search": {
                "name": "quick_search",
                "description": TOOL_DESC_QUICK_SEARCH,
                "parameters": {
                    "query": "Строка поискового запроса",
                    "limit": "Количество возвращаемых результатов (необязательно, по умолчанию 10)"
                }
            },
            "interview_agents": {
                "name": "interview_agents",
                "description": TOOL_DESC_INTERVIEW_AGENTS,
                "parameters": {
                    "interview_topic": "Тема интервью или описание задачи (например: 'Понять, что студенты думают о событии')",
                    "max_agents": "Максимальное число опрашиваемых агентов (необязательно, по умолчанию 5, максимум 10)"
                }
            }
        }
    
    def _execute_tool(self, tool_name: str, parameters: Dict[str, Any], report_context: str = "") -> str:
        """
        执行工具调用
        
        Args:
            tool_name: 工具名称
            parameters: 工具参数
            report_context: 报告上下文（用于InsightForge）
            
        Returns:
            工具执行结果（文本格式）
        """
        logger.info(f"Выполняю инструмент: {tool_name}, параметры: {parameters}")
        
        try:
            if tool_name == "insight_forge":
                query = parameters.get("query", "")
                ctx = parameters.get("report_context", "") or report_context
                result = self.zep_tools.insight_forge(
                    graph_id=self.graph_id,
                    query=query,
                    simulation_requirement=self.simulation_requirement,
                    report_context=ctx
                )
                return result.to_text()
            
            elif tool_name == "panorama_search":
                # 广度搜索 - 获取全貌
                query = parameters.get("query", "")
                include_expired = parameters.get("include_expired", True)
                if isinstance(include_expired, str):
                    include_expired = include_expired.lower() in ['true', '1', 'yes']
                result = self.zep_tools.panorama_search(
                    graph_id=self.graph_id,
                    query=query,
                    include_expired=include_expired
                )
                return result.to_text()
            
            elif tool_name == "quick_search":
                # 简单搜索 - 快速检索
                query = parameters.get("query", "")
                limit = parameters.get("limit", 10)
                if isinstance(limit, str):
                    limit = int(limit)
                result = self.zep_tools.quick_search(
                    graph_id=self.graph_id,
                    query=query,
                    limit=limit
                )
                return result.to_text()
            
            elif tool_name == "interview_agents":
                # 深度采访 - 调用真实的OASIS采访API获取模拟Agent的回答（双平台）
                interview_topic = parameters.get("interview_topic", parameters.get("query", ""))
                max_agents = parameters.get("max_agents", 5)
                if isinstance(max_agents, str):
                    max_agents = int(max_agents)
                max_agents = min(max_agents, 10)
                result = self.zep_tools.interview_agents(
                    simulation_id=self.simulation_id,
                    interview_requirement=interview_topic,
                    simulation_requirement=self.simulation_requirement,
                    max_agents=max_agents
                )
                return result.to_text()
            
            # ========== 向后兼容的旧工具（内部重定向到新工具） ==========
            
            elif tool_name == "search_graph":
                # 重定向到 quick_search
                logger.info("search_graph перенаправлен в quick_search")
                return self._execute_tool("quick_search", parameters, report_context)
            
            elif tool_name == "get_graph_statistics":
                result = self.zep_tools.get_graph_statistics(self.graph_id)
                return json.dumps(result, ensure_ascii=False, indent=2)
            
            elif tool_name == "get_entity_summary":
                entity_name = parameters.get("entity_name", "")
                result = self.zep_tools.get_entity_summary(
                    graph_id=self.graph_id,
                    entity_name=entity_name
                )
                return json.dumps(result, ensure_ascii=False, indent=2)
            
            elif tool_name == "get_simulation_context":
                # 重定向到 insight_forge，因为它更强大
                logger.info("get_simulation_context перенаправлен в insight_forge")
                query = parameters.get("query", self.simulation_requirement)
                return self._execute_tool("insight_forge", {"query": query}, report_context)
            
            elif tool_name == "get_entities_by_type":
                entity_type = parameters.get("entity_type", "")
                nodes = self.zep_tools.get_entities_by_type(
                    graph_id=self.graph_id,
                    entity_type=entity_type
                )
                result = [n.to_dict() for n in nodes]
                return json.dumps(result, ensure_ascii=False, indent=2)
            
            else:
                return f"Неизвестный инструмент: {tool_name}. Используй один из следующих: insight_forge, panorama_search, quick_search, interview_agents"
                
        except Exception as e:
            localized_error = localize_provider_error(e)
            logger.error(f"Ошибка выполнения инструмента: {tool_name}, ошибка: {localized_error}")
            return f"Ошибка выполнения инструмента: {localized_error}"
    
    # 合法的工具名称集合，用于裸 JSON 兜底解析时校验
    VALID_TOOL_NAMES = {"insight_forge", "panorama_search", "quick_search", "interview_agents"}

    def _parse_tool_calls(self, response: str) -> List[Dict[str, Any]]:
        """
        从LLM响应中解析工具调用

        支持的格式（按优先级）：
        1. <tool_call>{"name": "tool_name", "parameters": {...}}</tool_call>
        2. 裸 JSON（响应整体或单行就是一个工具调用 JSON）
        """
        tool_calls = []

        # 格式1: XML风格（标准格式）
        xml_pattern = r'<tool_call>\s*(\{.*?\})\s*</tool_call>'
        for match in re.finditer(xml_pattern, response, re.DOTALL):
            try:
                call_data = json.loads(match.group(1))
                tool_calls.append(call_data)
            except json.JSONDecodeError:
                pass

        if tool_calls:
            return tool_calls

        # 格式2: 兜底 - LLM 直接输出裸 JSON（没包 <tool_call> 标签）
        # 只在格式1未匹配时尝试，避免误匹配正文中的 JSON
        stripped = response.strip()
        if stripped.startswith('{') and stripped.endswith('}'):
            try:
                call_data = json.loads(stripped)
                if self._is_valid_tool_call(call_data):
                    tool_calls.append(call_data)
                    return tool_calls
            except json.JSONDecodeError:
                pass

        # 响应可能包含思考文字 + 裸 JSON，尝试提取最后一个 JSON 对象
        json_pattern = r'(\{"(?:name|tool)"\s*:.*?\})\s*$'
        match = re.search(json_pattern, stripped, re.DOTALL)
        if match:
            try:
                call_data = json.loads(match.group(1))
                if self._is_valid_tool_call(call_data):
                    tool_calls.append(call_data)
            except json.JSONDecodeError:
                pass

        return tool_calls

    def _is_valid_tool_call(self, data: dict) -> bool:
        """Проверяет, что разобранный JSON является валидным вызовом инструмента."""
        # Поддерживаются обе схемы: {"name": ..., "parameters": ...} и {"tool": ..., "params": ...}
        tool_name = data.get("name") or data.get("tool")
        if tool_name and tool_name in self.VALID_TOOL_NAMES:
            # 统一键名为 name / parameters
            if "tool" in data:
                data["name"] = data.pop("tool")
            if "params" in data and "parameters" not in data:
                data["parameters"] = data.pop("params")
            return True
        return False
    
    def _get_tools_description(self) -> str:
        """Формирует текстовое описание инструментов."""
        desc_parts = ["Доступные инструменты:"]
        for name, tool in self.tools.items():
            params_desc = ", ".join([f"{k}: {v}" for k, v in tool["parameters"].items()])
            desc_parts.append(f"- {name}: {tool['description']}")
            if params_desc:
                desc_parts.append(f"  Параметры: {params_desc}")
        return "\n".join(desc_parts)
    
    def plan_outline(
        self, 
        progress_callback: Optional[Callable] = None
    ) -> ReportOutline:
        """
        Планирует структуру отчета на основе simulation requirement.
        """
        logger.info("Начинаю планирование структуры отчета...")
        
        if progress_callback:
            progress_callback("planning", 0, "Анализирую задачу симуляции...")
        
        # 首先获取模拟上下文
        context = self.zep_tools.get_simulation_context(
            graph_id=self.graph_id,
            simulation_requirement=self.simulation_requirement
        )
        
        if progress_callback:
            progress_callback("planning", 30, "Генерирую структуру отчета...")
        
        system_prompt = PLAN_SYSTEM_PROMPT
        user_prompt = PLAN_USER_PROMPT_TEMPLATE.format(
            simulation_requirement=self.simulation_requirement,
            total_nodes=context.get('graph_statistics', {}).get('total_nodes', 0),
            total_edges=context.get('graph_statistics', {}).get('total_edges', 0),
            entity_types=list(context.get('graph_statistics', {}).get('entity_types', {}).keys()),
            total_entities=context.get('total_entities', 0),
            related_facts_json=json.dumps(context.get('related_facts', [])[:10], ensure_ascii=False, indent=2),
        )

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3
            )
            
            if progress_callback:
                progress_callback("planning", 80, "Разбираю структуру разделов...")
            
            # 解析大纲
            sections = []
            for section_data in response.get("sections", []):
                sections.append(ReportSection(
                    title=section_data.get("title", ""),
                    content=""
                ))
            
            outline = ReportOutline(
                title=response.get("title", "Прогнозный отчет по симуляции"),
                summary=response.get("summary", ""),
                sections=sections
            )
            outline = self._ensure_russian_outline(outline)
            
            if progress_callback:
                progress_callback("planning", 100, "Структура отчета готова")
            
            logger.info(f"Планирование структуры завершено: {len(sections)} разделов")
            return outline
            
        except Exception as e:
            localized_error = localize_provider_error(e)
            logger.error(f"Не удалось спланировать структуру отчета: {localized_error}")
            # Fallback-структура из трех разделов
            return ReportOutline(
                title="Прогнозный отчет о будущем",
                summary="Анализ будущих трендов и рисков на основе симуляции",
                sections=[
                    ReportSection(title="Сценарий прогноза и ключевые выводы"),
                    ReportSection(title="Прогноз поведения групп"),
                    ReportSection(title="Тренды и ключевые риски")
                ]
            )
    
    def _generate_section_react(
        self, 
        section: ReportSection,
        outline: ReportOutline,
        previous_sections: List[str],
        progress_callback: Optional[Callable] = None,
        section_index: int = 0
    ) -> str:
        """
        Генерирует один раздел отчета в режиме ReACT.
        """
        logger.info(f"Генерация раздела в режиме ReACT: {section.title}")
        
        # Лог начала раздела
        if self.report_logger:
            self.report_logger.log_section_start(section.title, section_index)
        
        system_prompt = SECTION_SYSTEM_PROMPT_TEMPLATE.format(
            report_title=outline.title,
            report_summary=outline.summary,
            simulation_requirement=self.simulation_requirement,
            section_title=section.title,
            tools_description=self._get_tools_description(),
        )

        # Формируем пользовательский промт, ограничивая предыдущие разделы
        if previous_sections:
            previous_parts = []
            for sec in previous_sections:
                # Каждый раздел ограничиваем 4000 символами
                truncated = sec[:4000] + "..." if len(sec) > 4000 else sec
                previous_parts.append(truncated)
            previous_content = "\n\n---\n\n".join(previous_parts)
        else:
            previous_content = "(Это первый раздел)"
        
        user_prompt = SECTION_USER_PROMPT_TEMPLATE.format(
            previous_content=previous_content,
            section_title=section.title,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        # ReACT-цикл
        tool_calls_count = 0
        max_iterations = 5  # 最大迭代轮数
        min_tool_calls = 3  # Минимум вызовов инструментов
        conflict_retries = 0  # Число конфликтов "инструмент + Final Answer" подряд
        used_tools = set()  # Имена уже использованных инструментов
        all_tools = {"insight_forge", "panorama_search", "quick_search", "interview_agents"}

        # Контекст отчета для генерации подвопросов в InsightForge
        report_context = f"Название раздела: {section.title}\nТребование к симуляции: {self.simulation_requirement}"
        
        for iteration in range(max_iterations):
            if progress_callback:
                progress_callback(
                    "generating", 
                    int((iteration / max_iterations) * 100),
                    f"Идет поиск данных и написание текста ({tool_calls_count}/{self.MAX_TOOL_CALLS_PER_SECTION})"
                )
            
            # Вызов LLM
            response = self.llm.chat(
                messages=messages,
                temperature=0.5,
                max_tokens=4096
            )

            # Проверяем, что LLM вернула содержательный ответ
            if response is None:
                logger.warning(f"Раздел {section.title}, итерация {iteration + 1}: LLM вернула None")
                # Если итерации еще остались, просим продолжить
                if iteration < max_iterations - 1:
                    messages.append({"role": "assistant", "content": "(пустой ответ)"})
                    messages.append({"role": "user", "content": "Продолжай генерацию содержимого."})
                    continue
                # На последней итерации переходим к принудительному завершению
                break

            logger.debug(f"Ответ LLM: {response[:200]}...")

            # 解析一次，复用结果
            tool_calls = self._parse_tool_calls(response)
            has_tool_calls = bool(tool_calls)
            has_final_answer = "Final Answer:" in response

            # ── Конфликт: в одном ответе и вызов инструмента, и Final Answer ──
            if has_tool_calls and has_final_answer:
                conflict_retries += 1
                logger.warning(
                    f"Раздел {section.title}, итерация {iteration+1}: "
                    f"LLM одновременно вернула вызов инструмента и Final Answer (конфликт #{conflict_retries})"
                )

                if conflict_retries <= 2:
                    # На первых двух конфликтах просим ответить заново
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": (
                            "Ошибка формата: в одном ответе нельзя совмещать вызов инструмента и Final Answer.\n"
                            "Нужно выбрать только одно:\n"
                            "- либо вызвать один инструмент через <tool_call> без Final Answer;\n"
                            "- либо вернуть итоговый текст, начиная с 'Final Answer:', без <tool_call>.\n"
                            "Ответь заново и выбери только один вариант."
                        ),
                    })
                    continue
                else:
                    # На третьем конфликте отрезаем ответ до первого tool_call и продолжаем
                    logger.warning(
                        f"Раздел {section.title}: после {conflict_retries} конфликтов "
                        "выполняю только первый вызов инструмента"
                    )
                    first_tool_end = response.find('</tool_call>')
                    if first_tool_end != -1:
                        response = response[:first_tool_end + len('</tool_call>')]
                        tool_calls = self._parse_tool_calls(response)
                        has_tool_calls = bool(tool_calls)
                    has_final_answer = False
                    conflict_retries = 0

            # Логируем ответ LLM
            if self.report_logger:
                self.report_logger.log_llm_response(
                    section_title=section.title,
                    section_index=section_index,
                    response=response,
                    iteration=iteration + 1,
                    has_tool_calls=has_tool_calls,
                    has_final_answer=has_final_answer
                )

            # ── Случай 1: получен Final Answer ──
            if has_final_answer:
                # Если инструментов использовано мало, просим продолжить поиск
                if tool_calls_count < min_tool_calls:
                    messages.append({"role": "assistant", "content": response})
                    unused_tools = all_tools - used_tools
                    unused_hint = f"(еще не использованы инструменты: {', '.join(unused_tools)})" if unused_tools else ""
                    messages.append({
                        "role": "user",
                        "content": REACT_INSUFFICIENT_TOOLS_MSG.format(
                            tool_calls_count=tool_calls_count,
                            min_tool_calls=min_tool_calls,
                            unused_hint=unused_hint,
                        ),
                    })
                    continue

                # Нормальное завершение раздела
                final_answer = response.split("Final Answer:")[-1].strip()
                final_answer = self._ensure_russian_text(final_answer, f"раздел {section_index}")
                logger.info(f"Раздел {section.title} завершен (вызовов инструментов: {tool_calls_count})")

                if self.report_logger:
                    self.report_logger.log_section_content(
                        section_title=section.title,
                        section_index=section_index,
                        content=final_answer,
                        tool_calls_count=tool_calls_count
                    )
                return final_answer

            # ── Случай 2: LLM вызывает инструмент ──
            if has_tool_calls:
                # Если лимит достигнут, больше инструменты не разрешаем
                if tool_calls_count >= self.MAX_TOOL_CALLS_PER_SECTION:
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": REACT_TOOL_LIMIT_MSG.format(
                            tool_calls_count=tool_calls_count,
                            max_tool_calls=self.MAX_TOOL_CALLS_PER_SECTION,
                        ),
                    })
                    continue

                # Выполняем только первый вызов инструмента
                call = tool_calls[0]
                if len(tool_calls) > 1:
                    logger.info(f"LLM попыталась вызвать {len(tool_calls)} инструментов, выполняю только первый: {call['name']}")

                if self.report_logger:
                    self.report_logger.log_tool_call(
                        section_title=section.title,
                        section_index=section_index,
                        tool_name=call["name"],
                        parameters=call.get("parameters", {}),
                        iteration=iteration + 1
                    )

                result = self._execute_tool(
                    call["name"],
                    call.get("parameters", {}),
                    report_context=report_context
                )

                if self.report_logger:
                    self.report_logger.log_tool_result(
                        section_title=section.title,
                        section_index=section_index,
                        tool_name=call["name"],
                        result=result,
                        iteration=iteration + 1
                    )

                tool_calls_count += 1
                used_tools.add(call['name'])

                # Подсказка про еще не использованные инструменты
                unused_tools = all_tools - used_tools
                unused_hint = ""
                if unused_tools and tool_calls_count < self.MAX_TOOL_CALLS_PER_SECTION:
                    unused_hint = REACT_UNUSED_TOOLS_HINT.format(unused_list=", ".join(unused_tools))

                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": REACT_OBSERVATION_TEMPLATE.format(
                        tool_name=call["name"],
                        result=result,
                        tool_calls_count=tool_calls_count,
                        max_tool_calls=self.MAX_TOOL_CALLS_PER_SECTION,
                        used_tools_str=", ".join(used_tools),
                        unused_hint=unused_hint,
                    ),
                })
                continue

            # ── Случай 3: нет ни tool_call, ни Final Answer ──
            messages.append({"role": "assistant", "content": response})

            if tool_calls_count < min_tool_calls:
                # Если инструментов пока мало, просим продолжить работу ими
                unused_tools = all_tools - used_tools
                unused_hint = f"(еще не использованы инструменты: {', '.join(unused_tools)})" if unused_tools else ""

                messages.append({
                    "role": "user",
                    "content": REACT_INSUFFICIENT_TOOLS_MSG_ALT.format(
                        tool_calls_count=tool_calls_count,
                        min_tool_calls=min_tool_calls,
                        unused_hint=unused_hint,
                    ),
                })
                continue

            # Если инструментов уже достаточно, принимаем ответ даже без префикса Final Answer
            logger.info(f"Раздел {section.title}: префикс 'Final Answer:' не найден, принимаю ответ как итоговый текст (вызовов инструментов: {tool_calls_count})")
            final_answer = response.strip()
            final_answer = self._ensure_russian_text(final_answer, f"раздел {section_index}")

            if self.report_logger:
                self.report_logger.log_section_content(
                    section_title=section.title,
                    section_index=section_index,
                    content=final_answer,
                    tool_calls_count=tool_calls_count
                )
            return final_answer
        
        # Если итерации закончились, принудительно просим финальный ответ
        logger.warning(f"Раздел {section.title} достиг максимального числа итераций, выполняю принудительное завершение")
        messages.append({"role": "user", "content": REACT_FORCE_FINAL_MSG})
        
        response = self.llm.chat(
            messages=messages,
            temperature=0.5,
            max_tokens=4096
        )

        # Проверяем ответ на принудительном завершении
        if response is None:
            logger.error(f"Раздел {section.title}: при принудительном завершении LLM вернула None")
            final_answer = "Раздел не удалось сгенерировать: LLM вернула пустой ответ. Попробуйте еще раз позже."
        elif "Final Answer:" in response:
            final_answer = response.split("Final Answer:")[-1].strip()
        else:
            final_answer = response

        final_answer = self._ensure_russian_text(final_answer, f"раздел {section_index}")
        
        # 记录章节内容生成完成日志
        if self.report_logger:
            self.report_logger.log_section_content(
                section_title=section.title,
                section_index=section_index,
                content=final_answer,
                tool_calls_count=tool_calls_count
            )
        
        return final_answer
    
    def generate_report(
        self, 
        progress_callback: Optional[Callable[[str, int, str], None]] = None,
        report_id: Optional[str] = None
    ) -> Report:
        """
        生成完整报告（分章节实时输出）
        
        每个章节生成完成后立即保存到文件夹，不需要等待整个报告完成。
        文件结构：
        reports/{report_id}/
            meta.json       - 报告元信息
            outline.json    - 报告大纲
            progress.json   - 生成进度
            section_01.md   - 第1章节
            section_02.md   - 第2章节
            ...
            full_report.md  - 完整报告
        
        Args:
            progress_callback: 进度回调函数 (stage, progress, message)
            report_id: 报告ID（可选，如果不传则自动生成）
            
        Returns:
            Report: 完整报告
        """
        import uuid
        
        # 如果没有传入 report_id，则自动生成
        if not report_id:
            report_id = f"report_{uuid.uuid4().hex[:12]}"
        start_time = datetime.now()
        
        report = Report(
            report_id=report_id,
            simulation_id=self.simulation_id,
            graph_id=self.graph_id,
            simulation_requirement=self.simulation_requirement,
            status=ReportStatus.PENDING,
            created_at=datetime.now().isoformat()
        )
        
        # 已完成的章节标题列表（用于进度追踪）
        completed_section_titles = []
        
        try:
            # 初始化：创建报告文件夹并保存初始状态
            ReportManager._ensure_report_folder(report_id)
            
            # 初始化日志记录器（结构化日志 agent_log.jsonl）
            self.report_logger = ReportLogger(report_id)
            self.report_logger.log_start(
                simulation_id=self.simulation_id,
                graph_id=self.graph_id,
                simulation_requirement=self.simulation_requirement
            )
            
            # 初始化控制台日志记录器（console_log.txt）
            self.console_logger = ReportConsoleLogger(report_id)
            
            ReportManager.update_progress(
                report_id, "pending", 0, "Инициализация отчета...",
                completed_sections=[]
            )
            ReportManager.save_report(report)
            
            # 阶段1: 规划大纲
            report.status = ReportStatus.PLANNING
            ReportManager.update_progress(
                report_id, "planning", 5, "Начинаю планирование структуры отчета...",
                completed_sections=[]
            )
            
            # 记录规划开始日志
            self.report_logger.log_planning_start()
            
            if progress_callback:
                progress_callback("planning", 0, "Начинаю планирование структуры отчета...")
            
            outline = self.plan_outline(
                progress_callback=lambda stage, prog, msg: 
                    progress_callback(stage, prog // 5, msg) if progress_callback else None
            )
            report.outline = outline
            
            # 记录规划完成日志
            self.report_logger.log_planning_complete(outline.to_dict())
            
            # 保存大纲到文件
            ReportManager.save_outline(report_id, outline)
            ReportManager.update_progress(
                report_id, "planning", 15, f"План готов, всего разделов: {len(outline.sections)}",
                completed_sections=[]
            )
            ReportManager.save_report(report)
            
            logger.info(f"План отчета сохранен: {report_id}/outline.json")
            
            # 阶段2: 逐章节生成（分章节保存）
            report.status = ReportStatus.GENERATING
            
            total_sections = len(outline.sections)
            generated_sections = []  # 保存内容用于上下文
            
            for i, section in enumerate(outline.sections):
                section_num = i + 1
                base_progress = 20 + int((i / total_sections) * 70)
                
                # 更新进度
                ReportManager.update_progress(
                    report_id, "generating", base_progress,
                    f"Генерирую раздел: {section.title} ({section_num}/{total_sections})",
                    current_section=section.title,
                    completed_sections=completed_section_titles
                )
                
                if progress_callback:
                    progress_callback(
                        "generating", 
                        base_progress, 
                        f"Генерирую раздел: {section.title} ({section_num}/{total_sections})"
                    )
                
                # 生成主章节内容
                section_content = self._generate_section_react(
                    section=section,
                    outline=outline,
                    previous_sections=generated_sections,
                    progress_callback=lambda stage, prog, msg:
                        progress_callback(
                            stage, 
                            base_progress + int(prog * 0.7 / total_sections),
                            msg
                        ) if progress_callback else None,
                    section_index=section_num
                )
                
                section.content = section_content
                generated_sections.append(f"## {section.title}\n\n{section_content}")

                # 保存章节
                ReportManager.save_section(report_id, section_num, section)
                completed_section_titles.append(section.title)

                # 记录章节完成日志
                full_section_content = f"## {section.title}\n\n{section_content}"

                if self.report_logger:
                    self.report_logger.log_section_full_complete(
                        section_title=section.title,
                        section_index=section_num,
                        full_content=full_section_content.strip()
                    )

                logger.info(f"Раздел сохранен: {report_id}/section_{section_num:02d}.md")
                
                # 更新进度
                ReportManager.update_progress(
                    report_id, "generating", 
                    base_progress + int(70 / total_sections),
                    f"Раздел {section.title} завершен",
                    current_section=None,
                    completed_sections=completed_section_titles
                )
            
            # 阶段3: 组装完整报告
            if progress_callback:
                progress_callback("generating", 95, "Собираю полный отчет...")
            
            ReportManager.update_progress(
                report_id, "generating", 95, "Собираю полный отчет...",
                completed_sections=completed_section_titles
            )
            
            # 使用ReportManager组装完整报告
            report.markdown_content = ReportManager.assemble_full_report(report_id, outline)
            report.status = ReportStatus.COMPLETED
            report.completed_at = datetime.now().isoformat()
            
            # 计算总耗时
            total_time_seconds = (datetime.now() - start_time).total_seconds()
            
            # 记录报告完成日志
            if self.report_logger:
                self.report_logger.log_report_complete(
                    total_sections=total_sections,
                    total_time_seconds=total_time_seconds
                )
            
            # 保存最终报告
            ReportManager.save_report(report)
            ReportManager.update_progress(
                report_id, "completed", 100, "Генерация отчета завершена",
                completed_sections=completed_section_titles
            )
            
            if progress_callback:
                progress_callback("completed", 100, "Генерация отчета завершена")
            
            logger.info(f"Генерация отчета завершена: {report_id}")
            
            # 关闭控制台日志记录器
            if self.console_logger:
                self.console_logger.close()
                self.console_logger = None
            
            return report
            
        except Exception as e:
            localized_error = localize_provider_error(e)
            logger.error(f"Не удалось сгенерировать отчет: {localized_error}")
            report.status = ReportStatus.FAILED
            report.error = localized_error
            
            # 记录错误日志
            if self.report_logger:
                self.report_logger.log_error(localized_error, "failed")
            
            # 保存失败状态
            try:
                ReportManager.save_report(report)
                ReportManager.update_progress(
                    report_id, "failed", -1, f"Генерация отчета завершилась ошибкой: {localized_error}",
                    completed_sections=completed_section_titles
                )
            except Exception:
                pass  # 忽略保存失败的错误
            
            # 关闭控制台日志记录器
            if self.console_logger:
                self.console_logger.close()
                self.console_logger = None
            
            return report
    
    def chat(
        self, 
        message: str,
        chat_history: List[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        与Report Agent对话
        
        在对话中Agent可以自主调用检索工具来回答问题
        
        Args:
            message: 用户消息
            chat_history: 对话历史
            
        Returns:
            {
                "response": "Ответ агента",
                "tool_calls": [调用的工具列表],
                "sources": [信息来源]
            }
        """
        logger.info(f"Диалог с Report Agent: {message[:50]}...")
        
        chat_history = chat_history or []
        
        # 获取已生成的报告内容
        report_content = ""
        try:
            report = ReportManager.get_report_by_simulation(self.simulation_id)
            if report and report.markdown_content:
                # 限制报告长度，避免上下文过长
                report_content = report.markdown_content[:15000]
                if len(report.markdown_content) > 15000:
                    report_content += "\n\n... [Содержимое отчета было сокращено] ..."
        except Exception as e:
            logger.warning(f"Не удалось получить содержимое отчета: {e}")
        
        system_prompt = CHAT_SYSTEM_PROMPT_TEMPLATE.format(
            simulation_requirement=self.simulation_requirement,
            report_content=report_content if report_content else "(отчет пока отсутствует)",
            tools_description=self._get_tools_description(),
        )

        # 构建消息
        messages = [{"role": "system", "content": system_prompt}]
        
        # 添加历史对话
        for h in chat_history[-10:]:  # 限制历史长度
            messages.append(h)
        
        # 添加用户消息
        messages.append({
            "role": "user", 
            "content": message
        })
        
        # ReACT循环（简化版）
        tool_calls_made = []
        max_iterations = 2  # 减少迭代轮数
        
        for iteration in range(max_iterations):
            response = self.llm.chat(
                messages=messages,
                temperature=0.5
            )
            
            # 解析工具调用
            tool_calls = self._parse_tool_calls(response)
            
            if not tool_calls:
                # 没有工具调用，直接返回响应
                clean_response = re.sub(r'<tool_call>.*?</tool_call>', '', response, flags=re.DOTALL)
                clean_response = re.sub(r'\[TOOL_CALL\].*?\)', '', clean_response)
                
                return {
                    "response": clean_response.strip(),
                    "tool_calls": tool_calls_made,
                    "sources": [tc.get("parameters", {}).get("query", "") for tc in tool_calls_made]
                }
            
            # 执行工具调用（限制数量）
            tool_results = []
            for call in tool_calls[:1]:  # 每轮最多执行1次工具调用
                if len(tool_calls_made) >= self.MAX_TOOL_CALLS_PER_CHAT:
                    break
                result = self._execute_tool(call["name"], call.get("parameters", {}))
                tool_results.append({
                    "tool": call["name"],
                    "result": result[:1500]  # 限制结果长度
                })
                tool_calls_made.append(call)
            
            # 将结果添加到消息
            messages.append({"role": "assistant", "content": response})
            observation = "\n".join([f"[Результат {r['tool']}]\n{r['result']}" for r in tool_results])
            messages.append({
                "role": "user",
                "content": observation + CHAT_OBSERVATION_SUFFIX
            })
        
        # 达到最大迭代，获取最终响应
        final_response = self.llm.chat(
            messages=messages,
            temperature=0.5
        )
        
        # 清理响应
        clean_response = re.sub(r'<tool_call>.*?</tool_call>', '', final_response, flags=re.DOTALL)
        clean_response = re.sub(r'\[TOOL_CALL\].*?\)', '', clean_response)
        
        return {
            "response": clean_response.strip(),
            "tool_calls": tool_calls_made,
            "sources": [tc.get("parameters", {}).get("query", "") for tc in tool_calls_made]
        }


class ReportManager:
    """
    报告管理器
    
    负责报告的持久化存储和检索
    
    文件结构（分章节输出）：
    reports/
      {report_id}/
        meta.json          - 报告元信息和状态
        outline.json       - 报告大纲
        progress.json      - 生成进度
        section_01.md      - 第1章节
        section_02.md      - 第2章节
        ...
        full_report.md     - 完整报告
    """
    
    # 报告存储目录
    REPORTS_DIR = os.path.join(Config.UPLOAD_FOLDER, 'reports')
    
    @classmethod
    def _ensure_reports_dir(cls):
        """Гарантирует существование корневого каталога отчетов."""
        os.makedirs(cls.REPORTS_DIR, exist_ok=True)
    
    @classmethod
    def _get_report_folder(cls, report_id: str) -> str:
        """Возвращает путь к каталогу отчета."""
        return os.path.join(cls.REPORTS_DIR, report_id)
    
    @classmethod
    def _ensure_report_folder(cls, report_id: str) -> str:
        """Гарантирует существование каталога отчета и возвращает путь."""
        folder = cls._get_report_folder(report_id)
        os.makedirs(folder, exist_ok=True)
        return folder
    
    @classmethod
    def _get_report_path(cls, report_id: str) -> str:
        """Возвращает путь к файлу метаданных отчета."""
        return os.path.join(cls._get_report_folder(report_id), "meta.json")
    
    @classmethod
    def _get_report_markdown_path(cls, report_id: str) -> str:
        """Возвращает путь к итоговому Markdown-файлу отчета."""
        return os.path.join(cls._get_report_folder(report_id), "full_report.md")
    
    @classmethod
    def _get_outline_path(cls, report_id: str) -> str:
        """Возвращает путь к файлу структуры отчета."""
        return os.path.join(cls._get_report_folder(report_id), "outline.json")
    
    @classmethod
    def _get_progress_path(cls, report_id: str) -> str:
        """Возвращает путь к файлу прогресса."""
        return os.path.join(cls._get_report_folder(report_id), "progress.json")
    
    @classmethod
    def _get_section_path(cls, report_id: str, section_index: int) -> str:
        """Возвращает путь к Markdown-файлу раздела."""
        return os.path.join(cls._get_report_folder(report_id), f"section_{section_index:02d}.md")
    
    @classmethod
    def _get_agent_log_path(cls, report_id: str) -> str:
        """Возвращает путь к agent log."""
        return os.path.join(cls._get_report_folder(report_id), "agent_log.jsonl")
    
    @classmethod
    def _get_console_log_path(cls, report_id: str) -> str:
        """Возвращает путь к консольному логу."""
        return os.path.join(cls._get_report_folder(report_id), "console_log.txt")
    
    @classmethod
    def get_console_log(cls, report_id: str, from_line: int = 0) -> Dict[str, Any]:
        """
        获取控制台日志内容
        
        这是报告生成过程中的控制台输出日志（INFO、WARNING等），
        与 agent_log.jsonl 的结构化日志不同。
        
        Args:
            report_id: 报告ID
            from_line: 从第几行开始读取（用于增量获取，0 表示从头开始）
            
        Returns:
            {
                "logs": [日志行列表],
                "total_lines": 总行数,
                "from_line": 起始行号,
                "has_more": 是否还有更多日志
            }
        """
        log_path = cls._get_console_log_path(report_id)
        
        if not os.path.exists(log_path):
            return {
                "logs": [],
                "total_lines": 0,
                "from_line": 0,
                "has_more": False
            }
        
        logs = []
        total_lines = 0
        
        with open(log_path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                total_lines = i + 1
                if i >= from_line:
                    # 保留原始日志行，去掉末尾换行符
                    logs.append(line.rstrip('\n\r'))
        
        return {
            "logs": logs,
            "total_lines": total_lines,
            "from_line": from_line,
            "has_more": False  # 已读取到末尾
        }
    
    @classmethod
    def get_console_log_stream(cls, report_id: str) -> List[str]:
        """
        获取完整的控制台日志（一次性获取全部）
        
        Args:
            report_id: 报告ID
            
        Returns:
            日志行列表
        """
        result = cls.get_console_log(report_id, from_line=0)
        return result["logs"]
    
    @classmethod
    def get_agent_log(cls, report_id: str, from_line: int = 0) -> Dict[str, Any]:
        """
        获取 Agent 日志内容
        
        Args:
            report_id: 报告ID
            from_line: 从第几行开始读取（用于增量获取，0 表示从头开始）
            
        Returns:
            {
                "logs": [日志条目列表],
                "total_lines": 总行数,
                "from_line": 起始行号,
                "has_more": 是否还有更多日志
            }
        """
        log_path = cls._get_agent_log_path(report_id)
        
        if not os.path.exists(log_path):
            return {
                "logs": [],
                "total_lines": 0,
                "from_line": 0,
                "has_more": False
            }
        
        logs = []
        total_lines = 0
        
        with open(log_path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                total_lines = i + 1
                if i >= from_line:
                    try:
                        log_entry = json.loads(line.strip())
                        logs.append(log_entry)
                    except json.JSONDecodeError:
                        # 跳过解析失败的行
                        continue
        
        return {
            "logs": logs,
            "total_lines": total_lines,
            "from_line": from_line,
            "has_more": False  # 已读取到末尾
        }
    
    @classmethod
    def get_agent_log_stream(cls, report_id: str) -> List[Dict[str, Any]]:
        """
        获取完整的 Agent 日志（用于一次性获取全部）
        
        Args:
            report_id: 报告ID
            
        Returns:
            日志条目列表
        """
        result = cls.get_agent_log(report_id, from_line=0)
        return result["logs"]
    
    @classmethod
    def save_outline(cls, report_id: str, outline: ReportOutline) -> None:
        """
        保存报告大纲
        
        在规划阶段完成后立即调用
        """
        cls._ensure_report_folder(report_id)
        
        with open(cls._get_outline_path(report_id), 'w', encoding='utf-8') as f:
            json.dump(outline.to_dict(), f, ensure_ascii=False, indent=2)
        
        logger.info(f"Структура отчета сохранена: {report_id}")
    
    @classmethod
    def save_section(
        cls,
        report_id: str,
        section_index: int,
        section: ReportSection
    ) -> str:
        """
        保存单个章节

        在每个章节生成完成后立即调用，实现分章节输出

        Args:
            report_id: 报告ID
            section_index: 章节索引（从1开始）
            section: 章节对象

        Returns:
            保存的文件路径
        """
        cls._ensure_report_folder(report_id)

        # 构建章节Markdown内容 - 清理可能存在的重复标题
        cleaned_content = cls._clean_section_content(section.content, section.title)
        md_content = f"## {section.title}\n\n"
        if cleaned_content:
            md_content += f"{cleaned_content}\n\n"

        # 保存文件
        file_suffix = f"section_{section_index:02d}.md"
        file_path = os.path.join(cls._get_report_folder(report_id), file_suffix)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(md_content)

        logger.info(f"Раздел отчета сохранен: {report_id}/{file_suffix}")
        return file_path
    
    @classmethod
    def _clean_section_content(cls, content: str, section_title: str) -> str:
        """
        清理章节内容
        
        1. 移除内容开头与章节标题重复的Markdown标题行
        2. 将所有 ### 及以下级别的标题转换为粗体文本
        
        Args:
            content: 原始内容
            section_title: 章节标题
            
        Returns:
            清理后的内容
        """
        import re
        
        if not content:
            return content
        
        content = content.strip()
        lines = content.split('\n')
        cleaned_lines = []
        skip_next_empty = False
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            
            # 检查是否是Markdown标题行
            heading_match = re.match(r'^(#{1,6})\s+(.+)$', stripped)
            
            if heading_match:
                level = len(heading_match.group(1))
                title_text = heading_match.group(2).strip()
                
                # 检查是否是与章节标题重复的标题（跳过前5行内的重复）
                if i < 5:
                    if title_text == section_title or title_text.replace(' ', '') == section_title.replace(' ', ''):
                        skip_next_empty = True
                        continue
                
                # 将所有级别的标题（#, ##, ###, ####等）转换为粗体
                # 因为章节标题由系统添加，内容中不应有任何标题
                cleaned_lines.append(f"**{title_text}**")
                cleaned_lines.append("")  # 添加空行
                continue
            
            # 如果上一行是被跳过的标题，且当前行为空，也跳过
            if skip_next_empty and stripped == '':
                skip_next_empty = False
                continue
            
            skip_next_empty = False
            cleaned_lines.append(line)
        
        # 移除开头的空行
        while cleaned_lines and cleaned_lines[0].strip() == '':
            cleaned_lines.pop(0)
        
        # 移除开头的分隔线
        while cleaned_lines and cleaned_lines[0].strip() in ['---', '***', '___']:
            cleaned_lines.pop(0)
            # 同时移除分隔线后的空行
            while cleaned_lines and cleaned_lines[0].strip() == '':
                cleaned_lines.pop(0)
        
        return '\n'.join(cleaned_lines)
    
    @classmethod
    def update_progress(
        cls, 
        report_id: str, 
        status: str, 
        progress: int, 
        message: str,
        current_section: str = None,
        completed_sections: List[str] = None
    ) -> None:
        """
        更新报告生成进度
        
        前端可以通过读取progress.json获取实时进度
        """
        cls._ensure_report_folder(report_id)
        
        progress_data = {
            "status": status,
            "progress": progress,
            "message": message,
            "current_section": current_section,
            "completed_sections": completed_sections or [],
            "updated_at": datetime.now().isoformat()
        }
        
        with open(cls._get_progress_path(report_id), 'w', encoding='utf-8') as f:
            json.dump(progress_data, f, ensure_ascii=False, indent=2)
    
    @classmethod
    def get_progress(cls, report_id: str) -> Optional[Dict[str, Any]]:
        """Возвращает прогресс генерации отчета."""
        path = cls._get_progress_path(report_id)
        
        if not os.path.exists(path):
            return None
        
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    @classmethod
    def get_generated_sections(cls, report_id: str) -> List[Dict[str, Any]]:
        """
        获取已生成的章节列表
        
        返回所有已保存的章节文件信息
        """
        folder = cls._get_report_folder(report_id)
        
        if not os.path.exists(folder):
            return []
        
        sections = []
        for filename in sorted(os.listdir(folder)):
            if filename.startswith('section_') and filename.endswith('.md'):
                file_path = os.path.join(folder, filename)
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                # 从文件名解析章节索引
                parts = filename.replace('.md', '').split('_')
                section_index = int(parts[1])

                sections.append({
                    "filename": filename,
                    "section_index": section_index,
                    "content": content
                })

        return sections
    
    @classmethod
    def assemble_full_report(cls, report_id: str, outline: ReportOutline) -> str:
        """
        组装完整报告
        
        从已保存的章节文件组装完整报告，并进行标题清理
        """
        folder = cls._get_report_folder(report_id)
        
        # 构建报告头部
        md_content = f"# {outline.title}\n\n"
        md_content += f"> {outline.summary}\n\n"
        md_content += f"---\n\n"
        
        # 按顺序读取所有章节文件
        sections = cls.get_generated_sections(report_id)
        for section_info in sections:
            md_content += section_info["content"]
        
        # 后处理：清理整个报告的标题问题
        md_content = cls._post_process_report(md_content, outline)
        
        # 保存完整报告
        full_path = cls._get_report_markdown_path(report_id)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        logger.info(f"Полный отчет собран: {report_id}")
        return md_content
    
    @classmethod
    def _post_process_report(cls, content: str, outline: ReportOutline) -> str:
        """
        后处理报告内容
        
        1. 移除重复的标题
        2. 保留报告主标题(#)和章节标题(##)，移除其他级别的标题(###, ####等)
        3. 清理多余的空行和分隔线
        
        Args:
            content: 原始报告内容
            outline: 报告大纲
            
        Returns:
            处理后的内容
        """
        import re
        
        lines = content.split('\n')
        processed_lines = []
        prev_was_heading = False
        
        # 收集大纲中的所有章节标题
        section_titles = set()
        for section in outline.sections:
            section_titles.add(section.title)
        
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            
            # 检查是否是标题行
            heading_match = re.match(r'^(#{1,6})\s+(.+)$', stripped)
            
            if heading_match:
                level = len(heading_match.group(1))
                title = heading_match.group(2).strip()
                
                # 检查是否是重复标题（在连续5行内出现相同内容的标题）
                is_duplicate = False
                for j in range(max(0, len(processed_lines) - 5), len(processed_lines)):
                    prev_line = processed_lines[j].strip()
                    prev_match = re.match(r'^(#{1,6})\s+(.+)$', prev_line)
                    if prev_match:
                        prev_title = prev_match.group(2).strip()
                        if prev_title == title:
                            is_duplicate = True
                            break
                
                if is_duplicate:
                    # 跳过重复标题及其后的空行
                    i += 1
                    while i < len(lines) and lines[i].strip() == '':
                        i += 1
                    continue
                
                # 标题层级处理：
                # - # (level=1) 只保留报告主标题
                # - ## (level=2) 保留章节标题
                # - ### 及以下 (level>=3) 转换为粗体文本
                
                if level == 1:
                    if title == outline.title:
                        # 保留报告主标题
                        processed_lines.append(line)
                        prev_was_heading = True
                    elif title in section_titles:
                        # 章节标题错误使用了#，修正为##
                        processed_lines.append(f"## {title}")
                        prev_was_heading = True
                    else:
                        # 其他一级标题转为粗体
                        processed_lines.append(f"**{title}**")
                        processed_lines.append("")
                        prev_was_heading = False
                elif level == 2:
                    if title in section_titles or title == outline.title:
                        # 保留章节标题
                        processed_lines.append(line)
                        prev_was_heading = True
                    else:
                        # 非章节的二级标题转为粗体
                        processed_lines.append(f"**{title}**")
                        processed_lines.append("")
                        prev_was_heading = False
                else:
                    # ### 及以下级别的标题转换为粗体文本
                    processed_lines.append(f"**{title}**")
                    processed_lines.append("")
                    prev_was_heading = False
                
                i += 1
                continue
            
            elif stripped == '---' and prev_was_heading:
                # 跳过标题后紧跟的分隔线
                i += 1
                continue
            
            elif stripped == '' and prev_was_heading:
                # 标题后只保留一个空行
                if processed_lines and processed_lines[-1].strip() != '':
                    processed_lines.append(line)
                prev_was_heading = False
            
            else:
                processed_lines.append(line)
                prev_was_heading = False
            
            i += 1
        
        # 清理连续的多个空行（保留最多2个）
        result_lines = []
        empty_count = 0
        for line in processed_lines:
            if line.strip() == '':
                empty_count += 1
                if empty_count <= 2:
                    result_lines.append(line)
            else:
                empty_count = 0
                result_lines.append(line)
        
        return '\n'.join(result_lines)
    
    @classmethod
    def save_report(cls, report: Report) -> None:
        """Сохраняет метаданные отчета и итоговый Markdown."""
        cls._ensure_report_folder(report.report_id)
        
        # 保存元信息JSON
        with open(cls._get_report_path(report.report_id), 'w', encoding='utf-8') as f:
            json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
        
        # 保存大纲
        if report.outline:
            cls.save_outline(report.report_id, report.outline)
        
        # 保存完整Markdown报告
        if report.markdown_content:
            with open(cls._get_report_markdown_path(report.report_id), 'w', encoding='utf-8') as f:
                f.write(report.markdown_content)
        
        logger.info(f"Отчет сохранен: {report.report_id}")
    
    @classmethod
    def get_report(cls, report_id: str) -> Optional[Report]:
        """Возвращает отчет по ID."""
        path = cls._get_report_path(report_id)
        
        if not os.path.exists(path):
            # 兼容旧格式：检查直接存储在reports目录下的文件
            old_path = os.path.join(cls.REPORTS_DIR, f"{report_id}.json")
            if os.path.exists(old_path):
                path = old_path
            else:
                return None
        
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 重建Report对象
        outline = None
        if data.get('outline'):
            outline_data = data['outline']
            sections = []
            for s in outline_data.get('sections', []):
                sections.append(ReportSection(
                    title=s['title'],
                    content=s.get('content', '')
                ))
            outline = ReportOutline(
                title=outline_data['title'],
                summary=outline_data['summary'],
                sections=sections
            )
        
        # 如果markdown_content为空，尝试从full_report.md读取
        markdown_content = data.get('markdown_content', '')
        if not markdown_content:
            full_report_path = cls._get_report_markdown_path(report_id)
            if os.path.exists(full_report_path):
                with open(full_report_path, 'r', encoding='utf-8') as f:
                    markdown_content = f.read()
        
        return Report(
            report_id=data['report_id'],
            simulation_id=data['simulation_id'],
            graph_id=data['graph_id'],
            simulation_requirement=data['simulation_requirement'],
            status=ReportStatus(data['status']),
            outline=outline,
            markdown_content=markdown_content,
            created_at=data.get('created_at', ''),
            completed_at=data.get('completed_at', ''),
            error=data.get('error')
        )
    
    @classmethod
    def get_report_by_simulation(cls, simulation_id: str) -> Optional[Report]:
        """Возвращает отчет по ID симуляции."""
        cls._ensure_reports_dir()
        
        for item in os.listdir(cls.REPORTS_DIR):
            item_path = os.path.join(cls.REPORTS_DIR, item)
            # 新格式：文件夹
            if os.path.isdir(item_path):
                report = cls.get_report(item)
                if report and report.simulation_id == simulation_id:
                    return report
            # 兼容旧格式：JSON文件
            elif item.endswith('.json'):
                report_id = item[:-5]
                report = cls.get_report(report_id)
                if report and report.simulation_id == simulation_id:
                    return report
        
        return None
    
    @classmethod
    def list_reports(cls, simulation_id: Optional[str] = None, limit: int = 50) -> List[Report]:
        """Возвращает список отчетов."""
        cls._ensure_reports_dir()
        
        reports = []
        for item in os.listdir(cls.REPORTS_DIR):
            item_path = os.path.join(cls.REPORTS_DIR, item)
            # 新格式：文件夹
            if os.path.isdir(item_path):
                report = cls.get_report(item)
                if report:
                    if simulation_id is None or report.simulation_id == simulation_id:
                        reports.append(report)
            # 兼容旧格式：JSON文件
            elif item.endswith('.json'):
                report_id = item[:-5]
                report = cls.get_report(report_id)
                if report:
                    if simulation_id is None or report.simulation_id == simulation_id:
                        reports.append(report)
        
        # 按创建时间倒序
        reports.sort(key=lambda r: r.created_at, reverse=True)
        
        return reports[:limit]
    
    @classmethod
    def delete_report(cls, report_id: str) -> bool:
        """Удаляет отчет целиком вместе с каталогом."""
        import shutil
        
        folder_path = cls._get_report_folder(report_id)
        
        # 新格式：删除整个文件夹
        if os.path.exists(folder_path) and os.path.isdir(folder_path):
            shutil.rmtree(folder_path)
            logger.info(f"Каталог отчета удален: {report_id}")
            return True
        
        # 兼容旧格式：删除单独的文件
        deleted = False
        old_json_path = os.path.join(cls.REPORTS_DIR, f"{report_id}.json")
        old_md_path = os.path.join(cls.REPORTS_DIR, f"{report_id}.md")
        
        if os.path.exists(old_json_path):
            os.remove(old_json_path)
            deleted = True
        if os.path.exists(old_md_path):
            os.remove(old_md_path)
            deleted = True
        
        return deleted
