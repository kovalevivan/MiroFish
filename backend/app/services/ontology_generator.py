"""
Сервис генерации онтологии.
Интерфейс 1: анализирует текст и строит типы сущностей и отношений для симуляции.
"""

import json
from typing import Dict, Any, List, Optional
from ..utils.llm_client import LLMClient


# Системный промт для генерации онтологии
ONTOLOGY_SYSTEM_PROMPT = """Ты эксперт по проектированию онтологий для графов знаний. Твоя задача: проанализировать текст и требования к симуляции, а затем спроектировать типы сущностей и типы отношений для **симуляции общественной дискуссии в социальных медиа**.

**Важно: верни только корректный JSON и ничего кроме JSON.**

## Контекст задачи

Мы строим **систему симуляции общественного обсуждения в социальных медиа**. В этой системе:
- каждая сущность представляет аккаунт или субъекта, который может высказываться, взаимодействовать и распространять информацию;
- сущности влияют друг на друга, делают репосты, комментируют и отвечают;
- нам нужно моделировать реакции сторон и траектории распространения информации.

Поэтому **сущности должны быть реальными субъектами, которые могут говорить и взаимодействовать в социальных медиа**.

**Допустимые сущности**:
- конкретные люди: публичные фигуры, участники событий, лидеры мнений, эксперты, обычные пользователи;
- компании и бизнесы, включая официальные аккаунты;
- организации: университеты, ассоциации, НКО, профсоюзы и т.д.;
- государственные органы и регуляторы;
- медиа: газеты, телеканалы, сайты, независимые медиа;
- сами платформы социальных медиа;
- представители конкретных групп: фан-клубы, сообщества выпускников, инициативные группы и т.д.

**Недопустимые сущности**:
- абстрактные понятия, например "общественное мнение", "эмоции", "тренд";
- темы или топики, например "академическая честность", "реформа образования";
- позиции или отношения, например "сторонники", "противники".

## Формат ответа

Верни JSON следующей структуры:

```json
{
    "entity_types": [
        {
            "name": "название типа сущности на английском в PascalCase",
            "description": "краткое описание на английском, до 100 символов",
            "attributes": [
                {
                    "name": "имя атрибута на английском в snake_case",
                    "type": "text",
                    "description": "описание атрибута"
                }
            ],
            "examples": ["пример сущности 1", "пример сущности 2"]
        }
    ],
    "edge_types": [
        {
            "name": "название типа отношения на английском в UPPER_SNAKE_CASE",
            "description": "краткое описание на английском, до 100 символов",
            "source_targets": [
                {"source": "тип источника", "target": "тип цели"}
            ],
            "attributes": []
        }
    ],
    "analysis_summary": "краткий анализ содержания текста на русском"
}
```

## Правила проектирования

### 1. Проектирование типов сущностей

**Нужно ровно 10 типов сущностей.**

**В наборе должны быть как конкретные типы, так и резервные базовые типы.**

Десять типов должны распределяться так:

A. **Резервные типы (обязательны, должны стоять последними двумя элементами списка)**:
   - `Person`: базовый тип для любого человека, если он не подходит под более конкретную категорию.
   - `Organization`: базовый тип для любой организации, если она не подходит под более конкретную категорию.

B. **Конкретные типы (8 штук, выводятся из содержания текста)**:
   - выдели основные роли, которые реально встречаются в материалах;
   - пример для академического кейса: `Student`, `Professor`, `University`;
   - пример для бизнес-кейса: `Company`, `CEO`, `Employee`.

**Зачем нужны резервные типы**:
- в тексте всегда будут встречаться люди и организации, которые не укладываются в узкие категории;
- такие люди должны попадать в `Person`;
- небольшие или временные объединения должны попадать в `Organization`.

**Принципы для конкретных типов**:
- выбирай роли, которые часто встречаются или критичны для сценария;
- типы должны иметь четкие границы и не дублировать друг друга;
- в `description` обязательно поясни отличие конкретного типа от резервного.

### 2. Проектирование типов отношений

- количество: от 6 до 10;
- отношения должны отражать реальные связи и взаимодействия в социальных медиа;
- `source_targets` должны быть совместимы с определенными тобой типами сущностей.

### 3. Проектирование атрибутов

- для каждого типа сущности нужно 1-3 ключевых атрибута;
- **нельзя** использовать зарезервированные имена: `name`, `uuid`, `group_id`, `created_at`, `summary`;
- допустимые примеры: `full_name`, `title`, `role`, `position`, `location`, `description`.

## Справочные примеры типов сущностей

**Люди, конкретные типы**:
- Student
- Professor
- Journalist
- Celebrity
- Executive
- Official
- Lawyer
- Doctor

**Люди, резервный тип**:
- Person

**Организации, конкретные типы**:
- University
- Company
- GovernmentAgency
- MediaOutlet
- Hospital
- School
- NGO

**Организации, резервный тип**:
- Organization

## Справочные примеры отношений

- WORKS_FOR
- STUDIES_AT
- AFFILIATED_WITH
- REPRESENTS
- REGULATES
- REPORTS_ON
- COMMENTS_ON
- RESPONDS_TO
- SUPPORTS
- OPPOSES
- COLLABORATES_WITH
- COMPETES_WITH
"""


class OntologyGenerator:
    """
    Генератор онтологии.
    Анализирует тексты и строит определения типов сущностей и отношений.
    """
    
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()
    
    def generate(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Генерирует онтологию.

        Args:
            document_texts: список текстов документов
            simulation_requirement: описание задачи симуляции
            additional_context: дополнительный контекст

        Returns:
            словарь онтологии с entity_types, edge_types и analysis_summary
        """
        # Формируем пользовательское сообщение
        user_message = self._build_user_message(
            document_texts, 
            simulation_requirement,
            additional_context
        )
        
        messages = [
            {"role": "system", "content": ONTOLOGY_SYSTEM_PROMPT},
            {"role": "user", "content": user_message}
        ]
        
        # Вызываем LLM
        result = self.llm_client.chat_json(
            messages=messages,
            temperature=0.3,
            max_tokens=4096
        )
        
        # Валидация и постобработка
        result = self._validate_and_process(result)
        
        return result
    
    # Максимальная длина текста, отправляемого в LLM
    MAX_TEXT_LENGTH_FOR_LLM = 50000
    
    def _build_user_message(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str]
    ) -> str:
        """Формирует пользовательское сообщение."""
        
        # Объединяем тексты
        combined_text = "\n\n---\n\n".join(document_texts)
        original_length = len(combined_text)
        
        # Если текст слишком длинный, обрезаем только LLM-контекст
        if len(combined_text) > self.MAX_TEXT_LENGTH_FOR_LLM:
            combined_text = combined_text[:self.MAX_TEXT_LENGTH_FOR_LLM]
            combined_text += (
                f"\n\n...(исходный текст содержит {original_length} символов; "
                f"для анализа онтологии использованы первые {self.MAX_TEXT_LENGTH_FOR_LLM})..."
            )

        message = f"""## Требование к симуляции

{simulation_requirement}

## Содержимое документов

{combined_text}
"""
        
        if additional_context:
            message += f"""
## Дополнительные пояснения

{additional_context}
"""
        
        message += """
На основе материалов выше спроектируй типы сущностей и типы отношений для симуляции общественной дискуссии.

**Обязательные правила**:
1. Верни ровно 10 типов сущностей.
2. Последние два типа обязательно должны быть `Person` и `Organization`.
3. Первые восемь типов должны быть конкретными и вытекать из текста.
4. Все типы сущностей должны описывать реальных субъектов, а не абстрактные понятия.
5. Для атрибутов нельзя использовать зарезервированные имена вроде `name`, `uuid`, `group_id`; используй альтернативы вроде `full_name` и `org_name`.
"""
        
        return message
    
    def _validate_and_process(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Проверяет и нормализует результат."""
        
        # Гарантируем наличие обязательных полей
        if "entity_types" not in result:
            result["entity_types"] = []
        if "edge_types" not in result:
            result["edge_types"] = []
        if "analysis_summary" not in result:
            result["analysis_summary"] = ""
        
        # Проверяем типы сущностей
        for entity in result["entity_types"]:
            if "attributes" not in entity:
                entity["attributes"] = []
            if "examples" not in entity:
                entity["examples"] = []
            # Ограничиваем длину description
            if len(entity.get("description", "")) > 100:
                entity["description"] = entity["description"][:97] + "..."
        
        # Проверяем типы отношений
        for edge in result["edge_types"]:
            if "source_targets" not in edge:
                edge["source_targets"] = []
            if "attributes" not in edge:
                edge["attributes"] = []
            if len(edge.get("description", "")) > 100:
                edge["description"] = edge["description"][:97] + "..."
        
        # Ограничения Zep API: максимум 10 типов сущностей и 10 типов связей
        MAX_ENTITY_TYPES = 10
        MAX_EDGE_TYPES = 10
        
        # Резервные типы
        person_fallback = {
            "name": "Person",
            "description": "Any individual person not fitting other specific person types.",
            "attributes": [
                {"name": "full_name", "type": "text", "description": "Full name of the person"},
                {"name": "role", "type": "text", "description": "Role or occupation"}
            ],
            "examples": ["ordinary citizen", "anonymous netizen"]
        }
        
        organization_fallback = {
            "name": "Organization",
            "description": "Any organization not fitting other specific organization types.",
            "attributes": [
                {"name": "org_name", "type": "text", "description": "Name of the organization"},
                {"name": "org_type", "type": "text", "description": "Type of organization"}
            ],
            "examples": ["small business", "community group"]
        }
        
        # Проверяем, присутствуют ли резервные типы
        entity_names = {e["name"] for e in result["entity_types"]}
        has_person = "Person" in entity_names
        has_organization = "Organization" in entity_names
        
        # Определяем, какие резервные типы нужно добавить
        fallbacks_to_add = []
        if not has_person:
            fallbacks_to_add.append(person_fallback)
        if not has_organization:
            fallbacks_to_add.append(organization_fallback)
        
        if fallbacks_to_add:
            current_count = len(result["entity_types"])
            needed_slots = len(fallbacks_to_add)
            
            # Если после добавления будет больше 10 типов, убираем лишние
            if current_count + needed_slots > MAX_ENTITY_TYPES:
                # Сколько типов нужно убрать
                to_remove = current_count + needed_slots - MAX_ENTITY_TYPES
                # Удаляем с конца, сохраняя более приоритетные конкретные типы
                result["entity_types"] = result["entity_types"][:-to_remove]
            
            # Добавляем резервные типы
            result["entity_types"].extend(fallbacks_to_add)
        
        # Финальная защитная нормализация по лимитам
        if len(result["entity_types"]) > MAX_ENTITY_TYPES:
            result["entity_types"] = result["entity_types"][:MAX_ENTITY_TYPES]
        
        if len(result["edge_types"]) > MAX_EDGE_TYPES:
            result["edge_types"] = result["edge_types"][:MAX_EDGE_TYPES]
        
        return result
    
    def generate_python_code(self, ontology: Dict[str, Any]) -> str:
        """
        Преобразует онтологию в Python-код по образцу `ontology.py`.

        Args:
            ontology: определение онтологии

        Returns:
            строка с Python-кодом
        """
        code_lines = [
            '"""',
            'Определения пользовательских типов сущностей',
            'Автоматически сгенерировано MiroFish для социальной симуляции',
            '"""',
            '',
            'from pydantic import Field',
            'from zep_cloud.external_clients.ontology import EntityModel, EntityText, EdgeModel',
            '',
            '',
            '# ============== Определения типов сущностей ==============',
            '',
        ]
        
        # Генерация типов сущностей
        for entity in ontology.get("entity_types", []):
            name = entity["name"]
            desc = entity.get("description", f"A {name} entity.")
            
            code_lines.append(f'class {name}(EntityModel):')
            code_lines.append(f'    """{desc}"""')
            
            attrs = entity.get("attributes", [])
            if attrs:
                for attr in attrs:
                    attr_name = attr["name"]
                    attr_desc = attr.get("description", attr_name)
                    code_lines.append(f'    {attr_name}: EntityText = Field(')
                    code_lines.append(f'        description="{attr_desc}",')
                    code_lines.append(f'        default=None')
                    code_lines.append(f'    )')
            else:
                code_lines.append('    pass')
            
            code_lines.append('')
            code_lines.append('')
        
        code_lines.append('# ============== Определения типов отношений ==============')
        code_lines.append('')
        
        # Генерация типов отношений
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            # 转换为PascalCase类名
            class_name = ''.join(word.capitalize() for word in name.split('_'))
            desc = edge.get("description", f"A {name} relationship.")
            
            code_lines.append(f'class {class_name}(EdgeModel):')
            code_lines.append(f'    """{desc}"""')
            
            attrs = edge.get("attributes", [])
            if attrs:
                for attr in attrs:
                    attr_name = attr["name"]
                    attr_desc = attr.get("description", attr_name)
                    code_lines.append(f'    {attr_name}: EntityText = Field(')
                    code_lines.append(f'        description="{attr_desc}",')
                    code_lines.append(f'        default=None')
                    code_lines.append(f'    )')
            else:
                code_lines.append('    pass')
            
            code_lines.append('')
            code_lines.append('')
        
        # 生成类型字典
        code_lines.append('# ============== Конфигурация типов ==============')
        code_lines.append('')
        code_lines.append('ENTITY_TYPES = {')
        for entity in ontology.get("entity_types", []):
            name = entity["name"]
            code_lines.append(f'    "{name}": {name},')
        code_lines.append('}')
        code_lines.append('')
        code_lines.append('EDGE_TYPES = {')
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            class_name = ''.join(word.capitalize() for word in name.split('_'))
            code_lines.append(f'    "{name}": {class_name},')
        code_lines.append('}')
        code_lines.append('')
        
        # 生成边的source_targets映射
        code_lines.append('EDGE_SOURCE_TARGETS = {')
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            source_targets = edge.get("source_targets", [])
            if source_targets:
                st_list = ', '.join([
                    f'{{"source": "{st.get("source", "Entity")}", "target": "{st.get("target", "Entity")}"}}'
                    for st in source_targets
                ])
                code_lines.append(f'    "{name}": [{st_list}],')
        code_lines.append('}')
        
        return '\n'.join(code_lines)
