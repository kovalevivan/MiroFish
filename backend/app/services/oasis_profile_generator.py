"""
Генератор Agent Profile для OASIS.
Преобразует сущности из графа Zep в профили агентов для симуляции.
"""

import json
import random
import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime

from openai import OpenAI
from ..config import Config
from ..utils.logger import get_logger
from ..utils.zep_client import create_zep_client
from .zep_entity_reader import EntityNode, ZepEntityReader

logger = get_logger('mirofish.oasis_profile')


@dataclass
class OasisAgentProfile:
    """Структура профиля агента OASIS."""
    # Общие поля
    user_id: int
    user_name: str
    name: str
    bio: str
    persona: str
    
    # Дополнительные поля для Reddit
    karma: int = 1000
    
    # Дополнительные поля для Twitter
    friend_count: int = 100
    follower_count: int = 150
    statuses_count: int = 500
    
    # Дополнительная информация о персонаже
    age: Optional[int] = None
    gender: Optional[str] = None
    mbti: Optional[str] = None
    country: Optional[str] = None
    profession: Optional[str] = None
    interested_topics: List[str] = field(default_factory=list)
    
    # Данные об исходной сущности
    source_entity_uuid: Optional[str] = None
    source_entity_type: Optional[str] = None
    
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    
    def to_reddit_format(self) -> Dict[str, Any]:
        """Преобразует профиль в формат Reddit."""
        profile = {
            "user_id": self.user_id,
            "username": self.user_name,
            "name": self.name,
            "bio": self.bio,
            "persona": self.persona,
            "karma": self.karma,
            "created_at": self.created_at,
        }
        
        # Добавляем расширенные поля, если они заданы
        if self.age:
            profile["age"] = self.age
        if self.gender:
            profile["gender"] = self.gender
        if self.mbti:
            profile["mbti"] = self.mbti
        if self.country:
            profile["country"] = self.country
        if self.profession:
            profile["profession"] = self.profession
        if self.interested_topics:
            profile["interested_topics"] = self.interested_topics
        
        return profile
    
    def to_twitter_format(self) -> Dict[str, Any]:
        """Преобразует профиль в формат Twitter."""
        profile = {
            "user_id": self.user_id,
            "username": self.user_name,
            "name": self.name,
            "bio": self.bio,
            "persona": self.persona,
            "friend_count": self.friend_count,
            "follower_count": self.follower_count,
            "statuses_count": self.statuses_count,
            "created_at": self.created_at,
        }
        
        # Добавляем расширенные поля
        if self.age:
            profile["age"] = self.age
        if self.gender:
            profile["gender"] = self.gender
        if self.mbti:
            profile["mbti"] = self.mbti
        if self.country:
            profile["country"] = self.country
        if self.profession:
            profile["profession"] = self.profession
        if self.interested_topics:
            profile["interested_topics"] = self.interested_topics
        
        return profile
    
    def to_dict(self) -> Dict[str, Any]:
        """Преобразует профиль в полный словарь."""
        return {
            "user_id": self.user_id,
            "user_name": self.user_name,
            "name": self.name,
            "bio": self.bio,
            "persona": self.persona,
            "karma": self.karma,
            "friend_count": self.friend_count,
            "follower_count": self.follower_count,
            "statuses_count": self.statuses_count,
            "age": self.age,
            "gender": self.gender,
            "mbti": self.mbti,
            "country": self.country,
            "profession": self.profession,
            "interested_topics": self.interested_topics,
            "source_entity_uuid": self.source_entity_uuid,
            "source_entity_type": self.source_entity_type,
            "created_at": self.created_at,
        }


class OasisProfileGenerator:
    """
    Генератор профилей OASIS.

    Преобразует сущности графа Zep в профили агентов для симуляции и,
    при необходимости, расширяет контекст через Zep и LLM.
    """
    
    # Список MBTI
    MBTI_TYPES = [
        "INTJ", "INTP", "ENTJ", "ENTP",
        "INFJ", "INFP", "ENFJ", "ENFP",
        "ISTJ", "ISFJ", "ESTJ", "ESFJ",
        "ISTP", "ISFP", "ESTP", "ESFP"
    ]
    
    # Часто используемые страны по умолчанию
    COUNTRIES = [
        "China", "US", "UK", "Japan", "Germany", "France", 
        "Canada", "Australia", "Brazil", "India", "South Korea"
    ]

    ENGLISH_FIRST_NAMES = [
        "Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Dylan",
        "Cameron", "Avery", "Parker", "Logan", "Quinn"
    ]
    ENGLISH_LAST_NAMES = [
        "Miller", "Parker", "Hayes", "Brooks", "Bennett", "Foster", "Reed",
        "Bailey", "Hunter", "Coleman", "Turner", "Evans"
    ]
    ORGANIZATION_PREFIXES = [
        "Civic", "Public", "Open", "Signal", "North", "Urban", "Daily",
        "Policy", "Insight", "Forum", "Global", "Modern"
    ]
    ORGANIZATION_SUFFIXES = [
        "Network", "Collective", "Desk", "Digest", "Watch", "Pulse",
        "Brief", "Studio", "Channel", "Alliance", "Lab", "Report"
    ]
    
    # Персональные сущности
    INDIVIDUAL_ENTITY_TYPES = [
        "student", "alumni", "professor", "person", "publicfigure", 
        "expert", "faculty", "official", "journalist", "activist"
    ]
    
    # Группы и институции
    GROUP_ENTITY_TYPES = [
        "university", "governmentagency", "organization", "ngo", 
        "mediaoutlet", "company", "institution", "group", "community"
    ]
    
    def __init__(
        self, 
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None,
        zep_api_key: Optional[str] = None,
        graph_id: Optional[str] = None
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
        
        # Клиент Zep для дополнительного контекста
        self.zep_api_key = zep_api_key or Config.ZEP_API_KEY
        self.zep_client = None
        self.graph_id = graph_id
        
        if self.zep_api_key:
            try:
                self.zep_client = create_zep_client(api_key=self.zep_api_key)
            except Exception as e:
                logger.warning(f"Не удалось инициализировать клиент Zep: {e}")
    
    def generate_profile_from_entity(
        self, 
        entity: EntityNode, 
        user_id: int,
        use_llm: bool = True
    ) -> OasisAgentProfile:
        """Создает OASIS Agent Profile из сущности Zep."""
        entity_type = entity.get_entity_type() or "Entity"
        
        # Базовая информация
        generated_name = profile_data.get("display_name") or self._generate_profile_display_name(entity.name, entity_type)
        user_name = profile_data.get("username") or self._generate_username(generated_name)
        
        # Формируем контекст
        context = self._build_entity_context(entity)
        
        if use_llm:
            # Генерация подробного профиля через LLM
            profile_data = self._generate_profile_with_llm(
                entity_name=name,
                entity_type=entity_type,
                entity_summary=entity.summary,
                entity_attributes=entity.attributes,
                context=context
            )
        else:
            # Шаблонная генерация профиля
            profile_data = self._generate_profile_rule_based(
                entity_name=name,
                entity_type=entity_type,
                entity_summary=entity.summary,
                entity_attributes=entity.attributes
            )
        
        return OasisAgentProfile(
            user_id=user_id,
            user_name=user_name,
            name=generated_name,
            bio=profile_data.get("bio", f"{entity_type}: {entity.name}"),
            persona=profile_data.get("persona", entity.summary or f"A {entity_type} named {entity.name}."),
            karma=profile_data.get("karma", random.randint(500, 5000)),
            friend_count=profile_data.get("friend_count", random.randint(50, 500)),
            follower_count=profile_data.get("follower_count", random.randint(100, 1000)),
            statuses_count=profile_data.get("statuses_count", random.randint(100, 2000)),
            age=profile_data.get("age"),
            gender=profile_data.get("gender"),
            mbti=profile_data.get("mbti"),
            country=profile_data.get("country"),
            profession=profile_data.get("profession"),
            interested_topics=profile_data.get("interested_topics", []),
            source_entity_uuid=entity.uuid,
            source_entity_type=entity_type,
        )
    
    def _normalize_ascii_words(self, text: str, separator: str = " ") -> str:
        cleaned = []
        prev_was_sep = False
        for char in text:
            if char.isascii() and char.isalnum():
                cleaned.append(char)
                prev_was_sep = False
            else:
                if not prev_was_sep:
                    cleaned.append(separator)
                    prev_was_sep = True
        normalized = ''.join(cleaned).strip(separator)
        while separator * 2 in normalized:
            normalized = normalized.replace(separator * 2, separator)
        return normalized

    def _generate_profile_display_name(self, entity_name: str, entity_type: str) -> str:
        """Формирует правдоподобное англоязычное имя соцпрофиля."""
        if self._is_individual_entity(entity_type):
            return f"{random.choice(self.ENGLISH_FIRST_NAMES)} {random.choice(self.ENGLISH_LAST_NAMES)}"
        return f"{random.choice(self.ORGANIZATION_PREFIXES)} {random.choice(self.ORGANIZATION_SUFFIXES)}"

    def _generate_username(self, name: str) -> str:
        """Генерирует ASCII-username, похожий на обычный handle."""
        username = self._normalize_ascii_words(name.lower(), separator="_")
        username = username.strip("_")
        if not username:
            username = "social_profile"
        suffix = random.randint(100, 999)
        return f"{username}_{suffix}"
    
    def _search_zep_for_entity(self, entity: EntityNode) -> Dict[str, Any]:
        """
        使用Zep图谱混合搜索功能获取实体相关的丰富信息
        
        Zep没有内置混合搜索接口，需要分别搜索edges和nodes然后合并结果。
        使用并行请求同时搜索，提高效率。
        
        Args:
            entity: 实体节点对象
            
        Returns:
            包含facts, node_summaries, context的字典
        """
        import concurrent.futures
        
        if not self.zep_client:
            return {"facts": [], "node_summaries": [], "context": ""}
        
        entity_name = entity.name
        
        results = {
            "facts": [],
            "node_summaries": [],
            "context": ""
        }
        
        # 必须有graph_id才能进行搜索
        if not self.graph_id:
            logger.debug("Пропускаю поиск в Zep: graph_id не задан")
            return results
        
        comprehensive_query = f"Вся информация, действия, события, связи и контекст, связанные с {entity_name}"
        
        def search_edges():
            """Ищет ребра (факты и связи) с повторными попытками."""
            max_retries = 3
            last_exception = None
            delay = 2.0
            
            for attempt in range(max_retries):
                try:
                    return self.zep_client.graph.search(
                        query=comprehensive_query,
                        graph_id=self.graph_id,
                        limit=30,
                        scope="edges",
                        reranker="rrf"
                    )
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        logger.debug(f"Ошибка поиска связей в Zep, попытка {attempt + 1}: {str(e)[:80]}, повторяю...")
                        time.sleep(delay)
                        delay *= 2
                    else:
                        logger.debug(f"Поиск связей в Zep не удался после {max_retries} попыток: {e}")
            return None
        
        def search_nodes():
            """Ищет узлы (сводки сущностей) с повторными попытками."""
            max_retries = 3
            last_exception = None
            delay = 2.0
            
            for attempt in range(max_retries):
                try:
                    return self.zep_client.graph.search(
                        query=comprehensive_query,
                        graph_id=self.graph_id,
                        limit=20,
                        scope="nodes",
                        reranker="rrf"
                    )
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        logger.debug(f"Ошибка поиска узлов в Zep, попытка {attempt + 1}: {str(e)[:80]}, повторяю...")
                        time.sleep(delay)
                        delay *= 2
                    else:
                        logger.debug(f"Поиск узлов в Zep не удался после {max_retries} попыток: {e}")
            return None
        
        try:
            # 并行执行edges和nodes搜索
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                edge_future = executor.submit(search_edges)
                node_future = executor.submit(search_nodes)
                
                # 获取结果
                edge_result = edge_future.result(timeout=30)
                node_result = node_future.result(timeout=30)
            
            # 处理边搜索结果
            all_facts = set()
            if edge_result and hasattr(edge_result, 'edges') and edge_result.edges:
                for edge in edge_result.edges:
                    if hasattr(edge, 'fact') and edge.fact:
                        all_facts.add(edge.fact)
            results["facts"] = list(all_facts)
            
            # 处理节点搜索结果
            all_summaries = set()
            if node_result and hasattr(node_result, 'nodes') and node_result.nodes:
                for node in node_result.nodes:
                    if hasattr(node, 'summary') and node.summary:
                        all_summaries.add(node.summary)
                    if hasattr(node, 'name') and node.name and node.name != entity_name:
                        all_summaries.add(f"Связанная сущность: {node.name}")
            results["node_summaries"] = list(all_summaries)
            
            # 构建综合上下文
            context_parts = []
            if results["facts"]:
                context_parts.append("Факты:\n" + "\n".join(f"- {f}" for f in results["facts"][:20]))
            if results["node_summaries"]:
                context_parts.append("Связанные сущности:\n" + "\n".join(f"- {s}" for s in results["node_summaries"][:10]))
            results["context"] = "\n\n".join(context_parts)
            
            logger.info(f"Смешанный поиск Zep завершен: {entity_name}, фактов {len(results['facts'])}, связанных узлов {len(results['node_summaries'])}")
            
        except concurrent.futures.TimeoutError:
            logger.warning(f"Таймаут поиска в Zep ({entity_name})")
        except Exception as e:
            logger.warning(f"Ошибка поиска в Zep ({entity_name}): {e}")
        
        return results
    
    def _build_entity_context(self, entity: EntityNode) -> str:
        """
        构建实体的完整上下文信息
        
        包括：
        1. 实体本身的边信息（事实）
        2. 关联节点的详细信息
        3. Zep混合检索到的丰富信息
        """
        context_parts = []
        
        # 1. 添加实体属性信息
        if entity.attributes:
            attrs = []
            for key, value in entity.attributes.items():
                if value and str(value).strip():
                    attrs.append(f"- {key}: {value}")
            if attrs:
                context_parts.append("### Атрибуты сущности\n" + "\n".join(attrs))
        
        # 2. 添加相关边信息（事实/关系）
        existing_facts = set()
        if entity.related_edges:
            relationships = []
            for edge in entity.related_edges:  # 不限制数量
                fact = edge.get("fact", "")
                edge_name = edge.get("edge_name", "")
                direction = edge.get("direction", "")
                
                if fact:
                    relationships.append(f"- {fact}")
                    existing_facts.add(fact)
                elif edge_name:
                    if direction == "outgoing":
                        relationships.append(f"- {entity.name} --[{edge_name}]--> (связанная сущность)")
                    else:
                        relationships.append(f"- (связанная сущность) --[{edge_name}]--> {entity.name}")
            
            if relationships:
                context_parts.append("### Связанные факты и отношения\n" + "\n".join(relationships))
        
        # 3. 添加关联节点的详细信息
        if entity.related_nodes:
            related_info = []
            for node in entity.related_nodes:  # 不限制数量
                node_name = node.get("name", "")
                node_labels = node.get("labels", [])
                node_summary = node.get("summary", "")
                
                # 过滤掉默认标签
                custom_labels = [l for l in node_labels if l not in ["Entity", "Node"]]
                label_str = f" ({', '.join(custom_labels)})" if custom_labels else ""
                
                if node_summary:
                    related_info.append(f"- **{node_name}**{label_str}: {node_summary}")
                else:
                    related_info.append(f"- **{node_name}**{label_str}")
            
            if related_info:
                context_parts.append("### Информация о связанных сущностях\n" + "\n".join(related_info))
        
        # 4. 使用Zep混合检索获取更丰富的信息
        zep_results = self._search_zep_for_entity(entity)
        
        if zep_results.get("facts"):
            # 去重：排除已存在的事实
            new_facts = [f for f in zep_results["facts"] if f not in existing_facts]
            if new_facts:
                context_parts.append("### Факты, найденные через Zep\n" + "\n".join(f"- {f}" for f in new_facts[:15]))
        
        if zep_results.get("node_summaries"):
            context_parts.append("### Узлы, найденные через Zep\n" + "\n".join(f"- {s}" for s in zep_results["node_summaries"][:10]))
        
        return "\n\n".join(context_parts)
    
    def _is_individual_entity(self, entity_type: str) -> bool:
        """Проверяет, относится ли сущность к персональному типу."""
        return entity_type.lower() in self.INDIVIDUAL_ENTITY_TYPES
    
    def _is_group_entity(self, entity_type: str) -> bool:
        """Проверяет, относится ли сущность к группе или организации."""
        return entity_type.lower() in self.GROUP_ENTITY_TYPES
    
    def _generate_profile_with_llm(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any],
        context: str
    ) -> Dict[str, Any]:
        """
        使用LLM生成非常详细的人设
        
        根据实体类型区分：
        - 个人实体：生成具体的人物设定
        - 群体/机构实体：生成代表性账号设定
        """
        
        is_individual = self._is_individual_entity(entity_type)
        
        if is_individual:
            prompt = self._build_individual_persona_prompt(
                entity_name, entity_type, entity_summary, entity_attributes, context
            )
        else:
            prompt = self._build_group_persona_prompt(
                entity_name, entity_type, entity_summary, entity_attributes, context
            )

        # 尝试多次生成，直到成功或达到最大重试次数
        max_attempts = 3
        last_error = None
        
        for attempt in range(max_attempts):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": self._get_system_prompt(is_individual)},
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.7 - (attempt * 0.1)  # 每次重试降低温度
                    # 不设置max_tokens，让LLM自由发挥
                )
                
                content = response.choices[0].message.content
                
                # 检查是否被截断（finish_reason不是'stop'）
                finish_reason = response.choices[0].finish_reason
                if finish_reason == 'length':
                    logger.warning(f"Вывод LLM был обрезан (attempt {attempt+1}), пробую восстановить...")
                    content = self._fix_truncated_json(content)
                
                # 尝试解析JSON
                try:
                    result = json.loads(content)
                    
                    # 验证必需字段
                    if "bio" not in result or not result["bio"]:
                        result["bio"] = entity_summary[:200] if entity_summary else f"{entity_type}: {entity_name}"
                    if "persona" not in result or not result["persona"]:
                        result["persona"] = entity_summary or f"{entity_name} — это {entity_type}."
                    
                    return result
                    
                except json.JSONDecodeError as je:
                    logger.warning(f"Не удалось разобрать JSON (attempt {attempt+1}): {str(je)[:80]}")
                    
                    # 尝试修复JSON
                    result = self._try_fix_json(content, entity_name, entity_type, entity_summary)
                    if result.get("_fixed"):
                        del result["_fixed"]
                        return result
                    
                    last_error = je
                    
            except Exception as e:
                logger.warning(f"Ошибка вызова LLM (attempt {attempt+1}): {str(e)[:80]}")
                last_error = e
                import time
                time.sleep(1 * (attempt + 1))  # 指数退避
        
        logger.warning(f"Не удалось сгенерировать профиль через LLM после {max_attempts} попыток: {last_error}. Перехожу к шаблонной генерации.")
        return self._generate_profile_rule_based(
            entity_name, entity_type, entity_summary, entity_attributes
        )
    
    def _fix_truncated_json(self, content: str) -> str:
        """Пытается восстановить JSON, обрезанный лимитом max_tokens."""
        import re
        
        # 如果JSON被截断，尝试闭合它
        content = content.strip()
        
        # 计算未闭合的括号
        open_braces = content.count('{') - content.count('}')
        open_brackets = content.count('[') - content.count(']')
        
        # 检查是否有未闭合的字符串
        # 简单检查：如果最后一个引号后没有逗号或闭合括号，可能是字符串被截断
        if content and content[-1] not in '",}]':
            # 尝试闭合字符串
            content += '"'
        
        # 闭合括号
        content += ']' * open_brackets
        content += '}' * open_braces
        
        return content
    
    def _try_fix_json(self, content: str, entity_name: str, entity_type: str, entity_summary: str = "") -> Dict[str, Any]:
        """Пытается починить поврежденный JSON."""
        import re
        
        # 1. 首先尝试修复被截断的情况
        content = self._fix_truncated_json(content)
        
        # 2. 尝试提取JSON部分
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            json_str = json_match.group()
            
            # 3. 处理字符串中的换行符问题
            # 找到所有字符串值并替换其中的换行符
            def fix_string_newlines(match):
                s = match.group(0)
                # 替换字符串内的实际换行符为空格
                s = s.replace('\n', ' ').replace('\r', ' ')
                # 替换多余空格
                s = re.sub(r'\s+', ' ', s)
                return s
            
            # 匹配JSON字符串值
            json_str = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', fix_string_newlines, json_str)
            
            # 4. 尝试解析
            try:
                result = json.loads(json_str)
                result["_fixed"] = True
                return result
            except json.JSONDecodeError as e:
                # 5. 如果还是失败，尝试更激进的修复
                try:
                    # 移除所有控制字符
                    json_str = re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', json_str)
                    # 替换所有连续空白
                    json_str = re.sub(r'\s+', ' ', json_str)
                    result = json.loads(json_str)
                    result["_fixed"] = True
                    return result
                except:
                    pass
        
        # 6. 尝试从内容中提取部分信息
        bio_match = re.search(r'"bio"\s*:\s*"([^"]*)"', content)
        persona_match = re.search(r'"persona"\s*:\s*"([^"]*)', content)  # 可能被截断
        
        bio = bio_match.group(1) if bio_match else (entity_summary[:200] if entity_summary else f"{entity_type}: {entity_name}")
        persona = persona_match.group(1) if persona_match else (entity_summary or f"{entity_name} — это {entity_type}.")
        
        # 如果提取到了有意义的内容，标记为已修复
        if bio_match or persona_match:
            logger.info("Удалось извлечь часть данных из поврежденного JSON")
            return {
                "bio": bio,
                "persona": persona,
                "_fixed": True
            }
        
        # 7. 完全失败，返回基础结构
        logger.warning("Не удалось восстановить JSON, возвращаю базовую структуру")
        return {
            "bio": entity_summary[:200] if entity_summary else f"{entity_type}: {entity_name}",
            "persona": entity_summary or f"{entity_name} — это {entity_type}."
        }
    
    def _get_system_prompt(self, is_individual: bool) -> str:
        """Возвращает системный prompt."""
        base_prompt = (
            "Ты эксперт по созданию реалистичных профилей пользователей соцсетей для симуляции общественных реакций. "
            "Сгенерируй детализированный и правдоподобный профиль. "
            "Текстовые поля bio/persona/profession/country/interested_topics возвращай на русском языке, "
            "а поля display_name и username делай естественными для Twitter/Reddit и только на латинице. "
            "Возвращай только валидный JSON. Значения строк не должны содержать необработанные переводы строк."
        )
        return base_prompt
    
    def _build_individual_persona_prompt(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any],
        context: str
    ) -> str:
        """Строит подробный prompt для персональной сущности."""
        
        attrs_str = json.dumps(entity_attributes, ensure_ascii=False) if entity_attributes else "нет"
        context_str = context[:3000] if context else "дополнительный контекст отсутствует"
        
        return f"""Сгенерируй подробный профиль пользователя соцсетей для сущности и максимально опирайся на уже известный контекст.

Название сущности: {entity_name}
Тип сущности: {entity_type}
Краткое описание: {entity_summary}
Атрибуты: {attrs_str}

Контекст:
{context_str}

Верни JSON со следующими полями:

1. display_name: правдоподобное имя профиля для Twitter/Reddit, только на латинице, без транслитерации исходного русского названия, выглядит как реальное имя человека
2. username: реалистичный handle для соцсетей, только ASCII, lowercase, буквы/цифры/подчеркивания, без символа @
3. bio: краткое описание профиля для соцсети, около 200 символов
4. persona: подробное описание персонажа сплошным текстом, включи:
   - базовые сведения: возраст, профессию, образование, место проживания
   - биографию: важные эпизоды, связь с событием, социальные связи
   - характер: тип MBTI, ключевые черты, способ выражения эмоций
   - поведение в соцсетях: частоту публикаций, любимые темы, стиль общения, языковые особенности
   - позицию по теме: отношение к обсуждаемому вопросу, что может его задеть или вдохновить
   - отличительные детали: любимые выражения, особый опыт, увлечения
   - личную память: как персонаж связан с событием и что уже делал или как реагировал
5. age: целое число
6. gender: только "male" или "female"
7. mbti: тип MBTI, например INTJ или ENFP
8. country: страна на русском языке
9. profession: профессия
10. interested_topics: массив интересующих тем

Важно:
- значения всех полей должны быть строками, числами или массивом строк без переводов строк внутри значений
- display_name и username не должны быть транслитерацией исходного русского названия сущности; придумай естественный англоязычный соцпрофиль, который соответствует роли и типу сущности
- persona должна быть одним связным абзацем
- весь текст на русском, кроме gender, display_name и username
- содержание должно строго соответствовать исходной сущности
- age должен быть корректным целым числом, gender только "male" или "female"
"""

    def _build_group_persona_prompt(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any],
        context: str
    ) -> str:
        """Строит подробный prompt для группы или организации."""
        
        attrs_str = json.dumps(entity_attributes, ensure_ascii=False) if entity_attributes else "нет"
        context_str = context[:3000] if context else "дополнительный контекст отсутствует"
        
        return f"""Сгенерируй подробный профиль аккаунта для институции или группы, максимально опираясь на известный контекст.

Название сущности: {entity_name}
Тип сущности: {entity_type}
Краткое описание: {entity_summary}
Атрибуты: {attrs_str}

Контекст:
{context_str}

Верни JSON со следующими полями:

1. display_name: правдоподобное англоязычное название аккаунта для Twitter/Reddit, только на латинице, не транслитерация исходного названия
2. username: реалистичный handle для соцсетей, только ASCII, lowercase, буквы/цифры/подчеркивания, без символа @
3. bio: краткое описание официального аккаунта, сдержанное и профессиональное
4. persona: подробное описание аккаунта сплошным текстом, включи:
   - сведения об организации: официальное название, природу организации, происхождение, основные функции
   - позиционирование аккаунта: тип аккаунта, целевую аудиторию, ключевую задачу
   - стиль коммуникации: характер формулировок, частые обороты, чувствительные темы
   - тип контента: что публикует, как часто, в какие периоды особенно активен
   - институциональную позицию: отношение к ключевым вопросам и способ реакции на споры
   - особые примечания: кого представляет, какие у команды привычки ведения аккаунта
   - институциональную память: как организация связана с событием и какие шаги или реакции уже проявляла
5. age: всегда 30
6. gender: всегда "other"
7. mbti: тип MBTI для описания стиля аккаунта
8. country: страна на русском языке
9. profession: описание функции или роли организации
10. interested_topics: массив основных тематик

Важно:
- все значения должны быть строками, числами или массивом строк, без null
- display_name и username должны выглядеть как реальный англоязычный брендовый соцаккаунт, а не как транслитерация русского названия
- persona должна быть одним связным абзацем без переводов строк
- весь текст на русском, кроме gender="other", display_name и username
- age должен быть равен 30, gender должен быть строкой "other"
- тон и содержание должны соответствовать типу институции"""
    
    def _generate_profile_rule_based(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Генерирует базовый профиль по правилам."""
        
        # Выбираем шаблон профиля по типу сущности
        entity_type_lower = entity_type.lower()
        
        if entity_type_lower in ["student", "alumni"]:
            return {
                "display_name": f"{random.choice(self.ENGLISH_FIRST_NAMES)} {random.choice(self.ENGLISH_LAST_NAMES)}",
                "username": f"{random.choice(['campus', 'study', 'daily', 'notes'])}_{random.randint(100, 999)}",
                "bio": f"{entity_type} with interests in academics and social issues.",
                "persona": f"{entity_name} is a {entity_type.lower()} who is actively engaged in academic and social discussions. They enjoy sharing perspectives and connecting with peers.",
                "age": random.randint(18, 30),
                "gender": random.choice(["male", "female"]),
                "mbti": random.choice(self.MBTI_TYPES),
                "country": random.choice(self.COUNTRIES),
                "profession": "Student",
                "interested_topics": ["Education", "Social Issues", "Technology"],
            }
        
        elif entity_type_lower in ["publicfigure", "expert", "faculty"]:
            return {
                "display_name": f"{random.choice(self.ENGLISH_FIRST_NAMES)} {random.choice(self.ENGLISH_LAST_NAMES)}",
                "username": f"{random.choice(['policy', 'insight', 'commentary', 'brief'])}_{random.randint(100, 999)}",
                "bio": f"Expert and thought leader in their field.",
                "persona": f"{entity_name} is a recognized {entity_type.lower()} who shares insights and opinions on important matters. They are known for their expertise and influence in public discourse.",
                "age": random.randint(35, 60),
                "gender": random.choice(["male", "female"]),
                "mbti": random.choice(["ENTJ", "INTJ", "ENTP", "INTP"]),
                "country": random.choice(self.COUNTRIES),
                "profession": entity_attributes.get("occupation", "Expert"),
                "interested_topics": ["Politics", "Economics", "Culture & Society"],
            }
        
        elif entity_type_lower in ["mediaoutlet", "socialmediaplatform"]:
            return {
                "display_name": f"{random.choice(self.ORGANIZATION_PREFIXES)} {random.choice(self.ORGANIZATION_SUFFIXES)}",
                "username": f"{random.choice(['newsdesk', 'signalfeed', 'dailywire', 'trendwatch'])}_{random.randint(100, 999)}",
                "bio": f"Official account for {entity_name}. News and updates.",
                "persona": f"{entity_name} is a media entity that reports news and facilitates public discourse. The account shares timely updates and engages with the audience on current events.",
                "age": 30,
                "gender": "other",
                "mbti": "ISTJ",
                "country": "Не указано",
                "profession": "Media",
                "interested_topics": ["General News", "Current Events", "Public Affairs"],
            }
        
        elif entity_type_lower in ["university", "governmentagency", "ngo", "organization"]:
            return {
                "display_name": f"{random.choice(self.ORGANIZATION_PREFIXES)} {random.choice(self.ORGANIZATION_SUFFIXES)}",
                "username": f"{random.choice(['publicoffice', 'civicdesk', 'policyforum', 'officialbrief'])}_{random.randint(100, 999)}",
                "bio": f"Official account of {entity_name}.",
                "persona": f"{entity_name} is an institutional entity that communicates official positions, announcements, and engages with stakeholders on relevant matters.",
                "age": 30,
                "gender": "other",
                "mbti": "ISTJ",
                "country": "Не указано",
                "profession": entity_type,
                "interested_topics": ["Public Policy", "Community", "Official Announcements"],
            }
        
        else:
            # 默认人设
            return {
                "display_name": self._generate_profile_display_name(entity_name, entity_type),
                "username": f"{random.choice(['social', 'public', 'civic', 'forum'])}_{random.randint(100, 999)}",
                "bio": entity_summary[:150] if entity_summary else f"{entity_type}: {entity_name}",
                "persona": entity_summary or f"{entity_name} is a {entity_type.lower()} participating in social discussions.",
                "age": random.randint(25, 50),
                "gender": random.choice(["male", "female"]),
                "mbti": random.choice(self.MBTI_TYPES),
                "country": random.choice(self.COUNTRIES),
                "profession": entity_type,
                "interested_topics": ["General", "Social Issues"],
            }
    
    def set_graph_id(self, graph_id: str):
        """Сохраняет graph_id для последующего поиска в Zep."""
        self.graph_id = graph_id
    
    def generate_profiles_from_entities(
        self,
        entities: List[EntityNode],
        use_llm: bool = True,
        progress_callback: Optional[callable] = None,
        graph_id: Optional[str] = None,
        parallel_count: int = 5,
        realtime_output_path: Optional[str] = None,
        output_platform: str = "reddit"
    ) -> List[OasisAgentProfile]:
        """Пакетно генерирует профили агентов из списка сущностей."""
        import concurrent.futures
        from threading import Lock
        
        # Сохраняем graph_id для поиска в Zep
        if graph_id:
            self.graph_id = graph_id
        
        total = len(entities)
        profiles = [None] * total
        completed_count = [0]
        lock = Lock()
        
        # Вспомогательная функция для промежуточного сохранения
        def save_profiles_realtime():
            """Сохраняет уже готовые профили на диск по мере генерации."""
            if not realtime_output_path:
                return
            
            with lock:
                # Берем только уже готовые профили
                existing_profiles = [p for p in profiles if p is not None]
                if not existing_profiles:
                    return
                
                try:
                    if output_platform == "reddit":
                        # Формат Reddit JSON
                        profiles_data = [p.to_reddit_format() for p in existing_profiles]
                        with open(realtime_output_path, 'w', encoding='utf-8') as f:
                            json.dump(profiles_data, f, ensure_ascii=False, indent=2)
                    else:
                        # Формат Twitter CSV
                        import csv
                        profiles_data = [p.to_twitter_format() for p in existing_profiles]
                        if profiles_data:
                            fieldnames = list(profiles_data[0].keys())
                            with open(realtime_output_path, 'w', encoding='utf-8', newline='') as f:
                                writer = csv.DictWriter(f, fieldnames=fieldnames)
                                writer.writeheader()
                                writer.writerows(profiles_data)
                except Exception as e:
                    logger.warning(f"Не удалось выполнить промежуточное сохранение профилей: {e}")
        
        def generate_single_profile(idx: int, entity: EntityNode) -> tuple:
            """Генерирует один профиль."""
            entity_type = entity.get_entity_type() or "Entity"
            
            try:
                profile = self.generate_profile_from_entity(
                    entity=entity,
                    user_id=idx,
                    use_llm=use_llm
                )
                
                # Печатаем сгенерированный профиль в консоль
                self._print_generated_profile(entity.name, entity_type, profile)
                
                return idx, profile, None
                
            except Exception as e:
                logger.error(f"Не удалось сгенерировать профиль для сущности {entity.name}: {str(e)}")
                # Создаем базовый профиль-заглушку
                fallback_profile = OasisAgentProfile(
                    user_id=idx,
                    user_name=self._generate_username(self._generate_profile_display_name(entity.name, entity_type)),
                    name=self._generate_profile_display_name(entity.name, entity_type),
                    bio=f"{entity_type}: {entity.name}",
                    persona=entity.summary or f"A participant in social discussions.",
                    source_entity_uuid=entity.uuid,
                    source_entity_type=entity_type,
                )
                return idx, fallback_profile, str(e)
        
        logger.info(f"Запускаю параллельную генерацию профилей агентов: total={total}, parallel={parallel_count}")
        print(f"\n{'='*60}")
        print(f"Запуск генерации профилей агентов: сущностей={total}, параллелизм={parallel_count}")
        print(f"{'='*60}\n")
        
        # Выполняем задачи в пуле потоков
        with concurrent.futures.ThreadPoolExecutor(max_workers=parallel_count) as executor:
            # Отправляем задачи в пул
            future_to_entity = {
                executor.submit(generate_single_profile, idx, entity): (idx, entity)
                for idx, entity in enumerate(entities)
            }
            
            # Собираем результаты
            for future in concurrent.futures.as_completed(future_to_entity):
                idx, entity = future_to_entity[future]
                entity_type = entity.get_entity_type() or "Entity"
                
                try:
                    result_idx, profile, error = future.result()
                    profiles[result_idx] = profile
                    
                    with lock:
                        completed_count[0] += 1
                        current = completed_count[0]
                    
                    # Промежуточное сохранение
                    save_profiles_realtime()
                    
                    if progress_callback:
                        progress_callback(
                            current, 
                            total, 
                            f"Готово {current}/{total}: {entity.name} ({entity_type})"
                        )
                    
                    if error:
                        logger.warning(f"[{current}/{total}] Для {entity.name} использован резервный профиль: {error}")
                    else:
                        logger.info(f"[{current}/{total}] Профиль успешно создан: {entity.name} ({entity_type})")
                        
                except Exception as e:
                    logger.error(f"Ошибка при обработке сущности {entity.name}: {str(e)}")
                    with lock:
                        completed_count[0] += 1
                    profiles[idx] = OasisAgentProfile(
                        user_id=idx,
                        user_name=self._generate_username(self._generate_profile_display_name(entity.name, entity_type)),
                        name=self._generate_profile_display_name(entity.name, entity_type),
                        bio=f"{entity_type}: {entity.name}",
                        persona=entity.summary or "A participant in social discussions.",
                        source_entity_uuid=entity.uuid,
                        source_entity_type=entity_type,
                    )
                    # Промежуточное сохранение даже для резервного профиля
                    save_profiles_realtime()
        
        print(f"\n{'='*60}")
        print(f"Генерация профилей завершена. Создано агентов: {len([p for p in profiles if p])}")
        print(f"{'='*60}\n")
        
        return profiles
    
    def _print_generated_profile(self, entity_name: str, entity_type: str, profile: OasisAgentProfile):
        """Выводит полный профиль в консоль без усечения."""
        separator = "-" * 70
        
        # Формируем полный вывод
        topics_str = ', '.join(profile.interested_topics) if profile.interested_topics else 'нет'
        
        output_lines = [
            f"\n{separator}",
            f"[Сгенерировано] {entity_name} ({entity_type})",
            f"{separator}",
            f"Имя пользователя: {profile.user_name}",
            f"",
            f"Краткое описание",
            f"{profile.bio}",
            f"",
            f"Подробный профиль",
            f"{profile.persona}",
            f"",
            f"Основные параметры",
            f"Возраст: {profile.age} | Пол: {profile.gender} | MBTI: {profile.mbti}",
            f"Профессия: {profile.profession} | Страна: {profile.country}",
            f"Интересы: {topics_str}",
            separator
        ]
        
        output = "\n".join(output_lines)
        
        # Печатаем только в консоль, без дублирования в logger
        print(output)
    
    def save_profiles(
        self,
        profiles: List[OasisAgentProfile],
        file_path: str,
        platform: str = "reddit"
    ):
        """Сохраняет профили в нужном формате в зависимости от платформы."""
        if platform == "twitter":
            self._save_twitter_csv(profiles, file_path)
        else:
            self._save_reddit_json(profiles, file_path)
    
    def _save_twitter_csv(self, profiles: List[OasisAgentProfile], file_path: str):
        """Сохраняет Twitter-профили в CSV-формате, совместимом с OASIS."""
        import csv
        
        # Гарантируем расширение .csv
        if not file_path.endswith('.csv'):
            file_path = file_path.replace('.json', '.csv')
        
        with open(file_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            
            # Заголовки CSV
            headers = ['user_id', 'name', 'username', 'user_char', 'description']
            writer.writerow(headers)
            
            # Строки данных
            for idx, profile in enumerate(profiles):
                # Полный профиль для внутреннего использования LLM
                user_char = profile.bio
                if profile.persona and profile.persona != profile.bio:
                    user_char = f"{profile.bio} {profile.persona}"
                # В CSV убираем переводы строк
                user_char = user_char.replace('\n', ' ').replace('\r', ' ')
                
                # Короткое описание для внешнего интерфейса
                description = profile.bio.replace('\n', ' ').replace('\r', ' ')
                
                row = [
                    idx,
                    profile.name,
                    profile.user_name,
                    user_char,
                    description
                ]
                writer.writerow(row)
        
        logger.info(f"Сохранено Twitter-профилей: {len(profiles)} -> {file_path}")
    
    def _normalize_gender(self, gender: Optional[str]) -> str:
        """Нормализует поле gender к допустимым значениям OASIS."""
        if not gender:
            return "other"
        
        gender_lower = gender.lower().strip()
        
        gender_map = {
            "male": "male",
            "female": "female",
            "other": "other",
        }
        
        return gender_map.get(gender_lower, "other")
    
    def _save_reddit_json(self, profiles: List[OasisAgentProfile], file_path: str):
        """Сохраняет Reddit-профили в JSON-формате."""
        data = []
        for idx, profile in enumerate(profiles):
            # Используем формат, совместимый с to_reddit_format()
            item = {
                "user_id": profile.user_id if profile.user_id is not None else idx,
                "username": profile.user_name,
                "name": profile.name,
                "bio": profile.bio[:150] if profile.bio else f"{profile.name}",
                "persona": profile.persona or f"{profile.name} is a participant in social discussions.",
                "karma": profile.karma if profile.karma else 1000,
                "created_at": profile.created_at,
                # Обязательные поля OASIS
                "age": profile.age if profile.age else 30,
                "gender": self._normalize_gender(profile.gender),
                "mbti": profile.mbti if profile.mbti else "ISTJ",
                "country": profile.country if profile.country else "Не указано",
            }
            
            # Опциональные поля
            if profile.profession:
                item["profession"] = profile.profession
            if profile.interested_topics:
                item["interested_topics"] = profile.interested_topics
            
            data.append(item)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Сохранено Reddit-профилей: {len(profiles)} -> {file_path}")
    
    # Старый метод оставлен как алиас
    def save_profiles_to_json(
        self,
        profiles: List[OasisAgentProfile],
        file_path: str,
        platform: str = "reddit"
    ):
        """Устаревший алиас для save_profiles()."""
        logger.warning("Метод save_profiles_to_json устарел, используй save_profiles")
        self.save_profiles(profiles, file_path, platform)
