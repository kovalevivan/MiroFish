"""
Сервис инструментов поиска по Zep.

Инкапсулирует поиск по графу, чтение узлов и ребер для Report Agent.
"""

import time
import json
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field

from ..config import Config
from ..utils.logger import get_logger
from ..utils.llm_client import LLMClient
from ..utils.zep_client import create_zep_client
from ..utils.zep_paging import fetch_all_nodes, fetch_all_edges

logger = get_logger('mirofish.zep_tools')


@dataclass
class SearchResult:
    """Результат поиска."""
    facts: List[str]
    edges: List[Dict[str, Any]]
    nodes: List[Dict[str, Any]]
    query: str
    total_count: int
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "facts": self.facts,
            "edges": self.edges,
            "nodes": self.nodes,
            "query": self.query,
            "total_count": self.total_count
        }
    
    def to_text(self) -> str:
        """Преобразует результат в текст для LLM."""
        text_parts = [f"Поисковый запрос: {self.query}", f"Найдено релевантных фрагментов: {self.total_count}"]
        
        if self.facts:
            text_parts.append("\n### Связанные факты:")
            for i, fact in enumerate(self.facts, 1):
                text_parts.append(f"{i}. {fact}")
        
        return "\n".join(text_parts)


@dataclass
class NodeInfo:
    """Информация об узле."""
    uuid: str
    name: str
    labels: List[str]
    summary: str
    attributes: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "labels": self.labels,
            "summary": self.summary,
            "attributes": self.attributes
        }
    
    def to_text(self) -> str:
        """Преобразует узел в текст."""
        entity_type = next((l for l in self.labels if l not in ["Entity", "Node"]), "Неизвестный тип")
        return f"Сущность: {self.name} (тип: {entity_type})\nКраткое описание: {self.summary}"


@dataclass
class EdgeInfo:
    """Информация о ребре."""
    uuid: str
    name: str
    fact: str
    source_node_uuid: str
    target_node_uuid: str
    source_node_name: Optional[str] = None
    target_node_name: Optional[str] = None
    # Временные атрибуты
    created_at: Optional[str] = None
    valid_at: Optional[str] = None
    invalid_at: Optional[str] = None
    expired_at: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "fact": self.fact,
            "source_node_uuid": self.source_node_uuid,
            "target_node_uuid": self.target_node_uuid,
            "source_node_name": self.source_node_name,
            "target_node_name": self.target_node_name,
            "created_at": self.created_at,
            "valid_at": self.valid_at,
            "invalid_at": self.invalid_at,
            "expired_at": self.expired_at
        }
    
    def to_text(self, include_temporal: bool = False) -> str:
        """Преобразует ребро в текст."""
        source = self.source_node_name or self.source_node_uuid[:8]
        target = self.target_node_name or self.target_node_uuid[:8]
        base_text = f"Связь: {source} --[{self.name}]--> {target}\nФакт: {self.fact}"
        
        if include_temporal:
            valid_at = self.valid_at or "Неизвестно"
            invalid_at = self.invalid_at or "по настоящее время"
            base_text += f"\nПериод действия: {valid_at} - {invalid_at}"
            if self.expired_at:
                base_text += f" (истекло: {self.expired_at})"
        
        return base_text
    
    @property
    def is_expired(self) -> bool:
        """Показывает, истекла ли связь."""
        return self.expired_at is not None
    
    @property
    def is_invalid(self) -> bool:
        """Показывает, помечена ли связь недействительной."""
        return self.invalid_at is not None


@dataclass
class InsightForgeResult:
    """Результат InsightForge с подвопросами и агрегированным анализом."""
    query: str
    simulation_requirement: str
    sub_queries: List[str]
    
    # Результаты по разным направлениям анализа
    semantic_facts: List[str] = field(default_factory=list)
    entity_insights: List[Dict[str, Any]] = field(default_factory=list)
    relationship_chains: List[str] = field(default_factory=list)
    
    # Статистика
    total_facts: int = 0
    total_entities: int = 0
    total_relationships: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "simulation_requirement": self.simulation_requirement,
            "sub_queries": self.sub_queries,
            "semantic_facts": self.semantic_facts,
            "entity_insights": self.entity_insights,
            "relationship_chains": self.relationship_chains,
            "total_facts": self.total_facts,
            "total_entities": self.total_entities,
            "total_relationships": self.total_relationships
        }
    
    def to_text(self) -> str:
        """Преобразует результат в подробный текст для LLM."""
        text_parts = [
            f"## Глубокий анализ сценария",
            f"Вопрос анализа: {self.query}",
            f"Сценарий симуляции: {self.simulation_requirement}",
            f"\n### Статистика анализа",
            f"- Релевантных фактов: {self.total_facts}",
            f"- Задействованных сущностей: {self.total_entities}",
            f"- Цепочек связей: {self.total_relationships}"
        ]
        
        # Подвопросы
        if self.sub_queries:
            text_parts.append(f"\n### Подвопросы анализа")
            for i, sq in enumerate(self.sub_queries, 1):
                text_parts.append(f"{i}. {sq}")
        
        # Семантические результаты
        if self.semantic_facts:
            text_parts.append(f"\n### Ключевые факты")
            for i, fact in enumerate(self.semantic_facts, 1):
                text_parts.append(f"{i}. \"{fact}\"")
        
        # Инсайты по сущностям
        if self.entity_insights:
            text_parts.append(f"\n### Ключевые сущности")
            for entity in self.entity_insights:
                text_parts.append(f"- **{entity.get('name', 'Неизвестно')}** ({entity.get('type', 'Сущность')})")
                if entity.get('summary'):
                    text_parts.append(f"  Описание: \"{entity.get('summary')}\"")
                if entity.get('related_facts'):
                    text_parts.append(f"  Связанных фактов: {len(entity.get('related_facts', []))}")
        
        # Цепочки связей
        if self.relationship_chains:
            text_parts.append(f"\n### Цепочки связей")
            for chain in self.relationship_chains:
                text_parts.append(f"- {chain}")
        
        return "\n".join(text_parts)


@dataclass
class PanoramaResult:
    """Результат Panorama с полным срезом, включая исторические данные."""
    query: str
    
    # 全部节点
    all_nodes: List[NodeInfo] = field(default_factory=list)
    # 全部边（包括过期的）
    all_edges: List[EdgeInfo] = field(default_factory=list)
    # 当前有效的事实
    active_facts: List[str] = field(default_factory=list)
    # 已过期/失效的事实（历史记录）
    historical_facts: List[str] = field(default_factory=list)
    
    # Статистика
    total_nodes: int = 0
    total_edges: int = 0
    active_count: int = 0
    historical_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "all_nodes": [n.to_dict() for n in self.all_nodes],
            "all_edges": [e.to_dict() for e in self.all_edges],
            "active_facts": self.active_facts,
            "historical_facts": self.historical_facts,
            "total_nodes": self.total_nodes,
            "total_edges": self.total_edges,
            "active_count": self.active_count,
            "historical_count": self.historical_count
        }
    
    def to_text(self) -> str:
        """Преобразует результат в полный текст без усечения."""
        text_parts = [
            f"## Панорамный обзор результатов",
            f"Запрос: {self.query}",
            f"\n### Статистика",
            f"- Всего узлов: {self.total_nodes}",
            f"- Всего связей: {self.total_edges}",
            f"- Актуальных фактов: {self.active_count}",
            f"- Исторических или истекших фактов: {self.historical_count}"
        ]
        
        # Актуальные факты
        if self.active_facts:
            text_parts.append(f"\n### Актуальные факты")
            for i, fact in enumerate(self.active_facts, 1):
                text_parts.append(f"{i}. \"{fact}\"")
        
        # Исторические и истекшие факты
        if self.historical_facts:
            text_parts.append(f"\n### Исторические или истекшие факты")
            for i, fact in enumerate(self.historical_facts, 1):
                text_parts.append(f"{i}. \"{fact}\"")
        
        # Ключевые сущности
        if self.all_nodes:
            text_parts.append(f"\n### Упомянутые сущности")
            for node in self.all_nodes:
                entity_type = next((l for l in node.labels if l not in ["Entity", "Node"]), "Сущность")
                text_parts.append(f"- **{node.name}** ({entity_type})")
        
        return "\n".join(text_parts)


@dataclass
class AgentInterview:
    """Результат интервью с одним агентом."""
    agent_name: str
    agent_role: str
    agent_bio: str
    question: str
    response: str
    key_quotes: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "agent_role": self.agent_role,
            "agent_bio": self.agent_bio,
            "question": self.question,
            "response": self.response,
            "key_quotes": self.key_quotes
        }
    
    def to_text(self) -> str:
        text = f"**{self.agent_name}** ({self.agent_role})\n"
        # Показываем полный bio без усечения
        text += f"_Кратко: {self.agent_bio}_\n\n"
        text += f"**Q:** {self.question}\n\n"
        text += f"**A:** {self.response}\n"
        if self.key_quotes:
            text += "\n**Ключевые цитаты:**\n"
            for quote in self.key_quotes:
                # Чистим кавычки
                clean_quote = quote.replace('\u201c', '').replace('\u201d', '').replace('"', '')
                clean_quote = clean_quote.replace('\u300c', '').replace('\u300d', '')
                clean_quote = clean_quote.strip()
                # Убираем ведущую пунктуацию
                while clean_quote and clean_quote[0] in '\uff0c,；;：:\u3001\u3002\uff01\uff1f\n\r\t ':
                    clean_quote = clean_quote[1:]
                # Отфильтровываем мусорные куски с нумерацией вопросов
                skip = False
                for d in '123456789':
                    if f'\u95ee\u9898{d}' in clean_quote:
                        skip = True
                        break
                if skip:
                    continue
                # При необходимости укорачиваем слишком длинные цитаты
                if len(clean_quote) > 150:
                    dot_pos = clean_quote.find('\u3002', 80)
                    if dot_pos > 0:
                        clean_quote = clean_quote[:dot_pos + 1]
                    else:
                        clean_quote = clean_quote[:147] + "..."
                if clean_quote and len(clean_quote) >= 10:
                    text += f'> "{clean_quote}"\n'
        return text


@dataclass
class InterviewResult:
    """Сводный результат интервью по нескольким агентам."""
    interview_topic: str
    interview_questions: List[str]
    
    # Выбранные для интервью агенты
    selected_agents: List[Dict[str, Any]] = field(default_factory=list)
    # Ответы агентов
    interviews: List[AgentInterview] = field(default_factory=list)
    
    # 选择Agent的理由
    selection_reasoning: str = ""
    # 整合后的采访摘要
    summary: str = ""
    
    # 统计
    total_agents: int = 0
    interviewed_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "interview_topic": self.interview_topic,
            "interview_questions": self.interview_questions,
            "selected_agents": self.selected_agents,
            "interviews": [i.to_dict() for i in self.interviews],
            "selection_reasoning": self.selection_reasoning,
            "summary": self.summary,
            "total_agents": self.total_agents,
            "interviewed_count": self.interviewed_count
        }
    
    def to_text(self) -> str:
        """Преобразует интервью в подробный текст для LLM и цитирования в отчете."""
        text_parts = [
            "## Отчет по глубинным интервью",
            f"**Тема интервью:** {self.interview_topic}",
            f"**Количество интервью:** {self.interviewed_count} / {self.total_agents} агентов",
            "\n### Почему были выбраны именно эти участники",
            self.selection_reasoning or "Выбрано автоматически",
            "\n---",
            "\n### Расшифровка интервью",
        ]

        if self.interviews:
            for i, interview in enumerate(self.interviews, 1):
                text_parts.append(f"\n#### Интервью #{i}: {interview.agent_name}")
                text_parts.append(interview.to_text())
                text_parts.append("\n---")
        else:
            text_parts.append("Интервью не проводились\n\n---")

        text_parts.append("\n### Краткие выводы и ключевые позиции")
        text_parts.append(self.summary or "Краткое резюме отсутствует")

        return "\n".join(text_parts)


class ZepToolsService:
    """
    Zep检索工具服务
    
    【核心检索工具 - 优化后】
    1. insight_forge - 深度洞察检索（最强大，自动生成子问题，多维度检索）
    2. panorama_search - 广度搜索（获取全貌，包括过期内容）
    3. quick_search - 简单搜索（快速检索）
    4. interview_agents - 深度采访（采访模拟Agent，获取多视角观点）
    
    【基础工具】
    - search_graph - 图谱语义搜索
    - get_all_nodes - 获取图谱所有节点
    - get_all_edges - 获取图谱所有边（含时间信息）
    - get_node_detail - 获取节点详细信息
    - get_node_edges - 获取节点相关的边
    - get_entities_by_type - 按类型获取实体
    - get_entity_summary - 获取实体的关系摘要
    """
    
    # 重试配置
    MAX_RETRIES = 3
    RETRY_DELAY = 2.0
    
    def __init__(self, api_key: Optional[str] = None, llm_client: Optional[LLMClient] = None):
        self.api_key = api_key or Config.ZEP_API_KEY
        if not self.api_key:
            raise ValueError("ZEP_API_KEY не настроен")
        
        self.client = create_zep_client(api_key=self.api_key)
        # LLM客户端用于InsightForge生成子问题
        self._llm_client = llm_client
        logger.info("ZepToolsService инициализирован")
    
    @property
    def llm(self) -> LLMClient:
        """Ленивая инициализация LLM-клиента."""
        if self._llm_client is None:
            self._llm_client = LLMClient()
        return self._llm_client
    
    def _call_with_retry(self, func, operation_name: str, max_retries: int = None):
        """Вызов API с повторными попытками."""
        max_retries = max_retries or self.MAX_RETRIES
        last_exception = None
        delay = self.RETRY_DELAY
        
        for attempt in range(max_retries):
            try:
                return func()
            except Exception as e:
                last_exception = e
                if attempt < max_retries - 1:
                    logger.warning(
                        f"Zep {operation_name}: попытка {attempt + 1} завершилась ошибкой: {str(e)[:100]}, "
                        f"повтор через {delay:.1f} с..."
                    )
                    time.sleep(delay)
                    delay *= 2
                else:
                    logger.error(f"Zep {operation_name}: ошибка после {max_retries} попыток: {str(e)}")
        
        raise last_exception
    
    def search_graph(
        self, 
        graph_id: str, 
        query: str, 
        limit: int = 10,
        scope: str = "edges"
    ) -> SearchResult:
        """
        图谱语义搜索
        
        使用混合搜索（语义+BM25）在图谱中搜索相关信息。
        如果Zep Cloud的search API不可用，则降级为本地关键词匹配。
        
        Args:
            graph_id: 图谱ID (Standalone Graph)
            query: 搜索查询
            limit: 返回结果数量
            scope: область поиска, `"edges"` или `"nodes"`
            
        Returns:
            SearchResult: 搜索结果
        """
        logger.info(f"Поиск по графу: graph_id={graph_id}, query={query[:50]}...")
        
        # 尝试使用Zep Cloud Search API
        try:
            search_results = self._call_with_retry(
                func=lambda: self.client.graph.search(
                    graph_id=graph_id,
                    query=query,
                    limit=limit,
                    scope=scope,
                    reranker="cross_encoder"
                ),
                operation_name=f"поиск по графу(graph={graph_id})"
            )
            
            facts = []
            edges = []
            nodes = []
            
            # 解析边搜索结果
            if hasattr(search_results, 'edges') and search_results.edges:
                for edge in search_results.edges:
                    if hasattr(edge, 'fact') and edge.fact:
                        facts.append(edge.fact)
                    edges.append({
                        "uuid": getattr(edge, 'uuid_', None) or getattr(edge, 'uuid', ''),
                        "name": getattr(edge, 'name', ''),
                        "fact": getattr(edge, 'fact', ''),
                        "source_node_uuid": getattr(edge, 'source_node_uuid', ''),
                        "target_node_uuid": getattr(edge, 'target_node_uuid', ''),
                    })
            
            # 解析节点搜索结果
            if hasattr(search_results, 'nodes') and search_results.nodes:
                for node in search_results.nodes:
                    nodes.append({
                        "uuid": getattr(node, 'uuid_', None) or getattr(node, 'uuid', ''),
                        "name": getattr(node, 'name', ''),
                        "labels": getattr(node, 'labels', []),
                        "summary": getattr(node, 'summary', ''),
                    })
                    # 节点摘要也算作事实
                    if hasattr(node, 'summary') and node.summary:
                        facts.append(f"[{node.name}]: {node.summary}")
            
            logger.info(f"Поиск завершен: найдено релевантных фактов {len(facts)}")
            
            return SearchResult(
                facts=facts,
                edges=edges,
                nodes=nodes,
                query=query,
                total_count=len(facts)
            )
            
        except Exception as e:
            logger.warning(f"Ошибка Zep Search API, перехожу к локальному поиску: {str(e)}")
            # 降级：使用本地关键词匹配搜索
            return self._local_search(graph_id, query, limit, scope)
    
    def _local_search(
        self, 
        graph_id: str, 
        query: str, 
        limit: int = 10,
        scope: str = "edges"
    ) -> SearchResult:
        """
        本地关键词匹配搜索（作为Zep Search API的降级方案）
        
        获取所有边/节点，然后在本地进行关键词匹配
        
        Args:
            graph_id: 图谱ID
            query: 搜索查询
            limit: 返回结果数量
            scope: 搜索范围
            
        Returns:
            SearchResult: 搜索结果
        """
        logger.info(f"Локальный поиск: query={query[:30]}...")
        
        facts = []
        edges_result = []
        nodes_result = []
        
        # 提取查询关键词（简单分词）
        query_lower = query.lower()
        keywords = [w.strip() for w in query_lower.replace(',', ' ').replace('，', ' ').split() if len(w.strip()) > 1]
        
        def match_score(text: str) -> int:
            """Считает score совпадения текста с запросом."""
            if not text:
                return 0
            text_lower = text.lower()
            # Полное совпадение
            if query_lower in text_lower:
                return 100
            # Совпадение по ключевым словам
            score = 0
            for keyword in keywords:
                if keyword in text_lower:
                    score += 10
            return score
        
        try:
            if scope in ["edges", "both"]:
                # Получаем все ребра и считаем score
                all_edges = self.get_all_edges(graph_id)
                scored_edges = []
                for edge in all_edges:
                    score = match_score(edge.fact) + match_score(edge.name)
                    if score > 0:
                        scored_edges.append((score, edge))
                
                # Сортируем по score
                scored_edges.sort(key=lambda x: x[0], reverse=True)
                
                for score, edge in scored_edges[:limit]:
                    if edge.fact:
                        facts.append(edge.fact)
                    edges_result.append({
                        "uuid": edge.uuid,
                        "name": edge.name,
                        "fact": edge.fact,
                        "source_node_uuid": edge.source_node_uuid,
                        "target_node_uuid": edge.target_node_uuid,
                    })
            
            if scope in ["nodes", "both"]:
                # Получаем все узлы и считаем score
                all_nodes = self.get_all_nodes(graph_id)
                scored_nodes = []
                for node in all_nodes:
                    score = match_score(node.name) + match_score(node.summary)
                    if score > 0:
                        scored_nodes.append((score, node))
                
                scored_nodes.sort(key=lambda x: x[0], reverse=True)
                
                for score, node in scored_nodes[:limit]:
                    nodes_result.append({
                        "uuid": node.uuid,
                        "name": node.name,
                        "labels": node.labels,
                        "summary": node.summary,
                    })
                    if node.summary:
                        facts.append(f"[{node.name}]: {node.summary}")
            
            logger.info(f"Локальный поиск завершен: найдено {len(facts)} релевантных фактов")
            
        except Exception as e:
            logger.error(f"Локальный поиск завершился ошибкой: {str(e)}")
        
        return SearchResult(
            facts=facts,
            edges=edges_result,
            nodes=nodes_result,
            query=query,
            total_count=len(facts)
        )
    
    def get_all_nodes(self, graph_id: str) -> List[NodeInfo]:
        """
        Возвращает все узлы графа постранично.

        Args:
            graph_id: ID графа

        Returns:
            Список узлов
        """
        logger.info(f"Получаю все узлы графа {graph_id}...")

        nodes = fetch_all_nodes(self.client, graph_id)

        result = []
        for node in nodes:
            node_uuid = getattr(node, 'uuid_', None) or getattr(node, 'uuid', None) or ""
            result.append(NodeInfo(
                uuid=str(node_uuid) if node_uuid else "",
                name=node.name or "",
                labels=node.labels or [],
                summary=node.summary or "",
                attributes=node.attributes or {}
            ))

        logger.info(f"Получено узлов: {len(result)}")
        return result

    def get_all_edges(self, graph_id: str, include_temporal: bool = True) -> List[EdgeInfo]:
        """
        Возвращает все ребра графа постранично, при необходимости с временными полями.

        Args:
            graph_id: ID графа
            include_temporal: включать ли временные поля

        Returns:
            Список ребер
        """
        logger.info(f"Получаю все ребра графа {graph_id}...")

        edges = fetch_all_edges(self.client, graph_id)

        result = []
        for edge in edges:
            edge_uuid = getattr(edge, 'uuid_', None) or getattr(edge, 'uuid', None) or ""
            edge_info = EdgeInfo(
                uuid=str(edge_uuid) if edge_uuid else "",
                name=edge.name or "",
                fact=edge.fact or "",
                source_node_uuid=edge.source_node_uuid or "",
                target_node_uuid=edge.target_node_uuid or ""
            )

            # Добавляем временные поля
            if include_temporal:
                edge_info.created_at = getattr(edge, 'created_at', None)
                edge_info.valid_at = getattr(edge, 'valid_at', None)
                edge_info.invalid_at = getattr(edge, 'invalid_at', None)
                edge_info.expired_at = getattr(edge, 'expired_at', None)

            result.append(edge_info)

        logger.info(f"Получено ребер: {len(result)}")
        return result
    
    def get_node_detail(self, node_uuid: str) -> Optional[NodeInfo]:
        """
        Возвращает подробную информацию об одном узле.
        
        Args:
            node_uuid: UUID узла
            
        Returns:
            Информация об узле или None
        """
        logger.info(f"Получаю детали узла: {node_uuid[:8]}...")
        
        try:
            node = self._call_with_retry(
                func=lambda: self.client.graph.node.get(uuid_=node_uuid),
                operation_name=f"получение деталей узла(uuid={node_uuid[:8]}...)"
            )
            
            if not node:
                return None
            
            return NodeInfo(
                uuid=getattr(node, 'uuid_', None) or getattr(node, 'uuid', ''),
                name=node.name or "",
                labels=node.labels or [],
                summary=node.summary or "",
                attributes=node.attributes or {}
            )
        except Exception as e:
            logger.error(f"Не удалось получить детали узла: {str(e)}")
            return None
    
    def get_node_edges(self, graph_id: str, node_uuid: str) -> List[EdgeInfo]:
        """
        Возвращает все ребра, связанные с указанным узлом.
        
        Args:
            graph_id: ID графа
            node_uuid: UUID узла
            
        Returns:
            Список ребер
        """
        logger.info(f"Получаю ребра для узла {node_uuid[:8]}...")
        
        try:
            # Получаем все ребра графа и фильтруем
            all_edges = self.get_all_edges(graph_id)
            
            result = []
            for edge in all_edges:
                # Проверяем, связано ли ребро с заданным узлом
                if edge.source_node_uuid == node_uuid or edge.target_node_uuid == node_uuid:
                    result.append(edge)
            
            logger.info(f"Найдено ребер, связанных с узлом: {len(result)}")
            return result
            
        except Exception as e:
            logger.warning(f"Не удалось получить ребра узла: {str(e)}")
            return []
    
    def get_entities_by_type(
        self, 
        graph_id: str, 
        entity_type: str
    ) -> List[NodeInfo]:
        """
        Возвращает сущности по типу.
        
        Args:
            graph_id: ID графа
            entity_type: тип сущности
            
        Returns:
            Список подходящих сущностей
        """
        logger.info(f"Получаю сущности типа {entity_type}...")
        
        all_nodes = self.get_all_nodes(graph_id)
        
        filtered = []
        for node in all_nodes:
            # Проверяем наличие нужной метки
            if entity_type in node.labels:
                filtered.append(node)
        
        logger.info(f"Найдено сущностей типа {entity_type}: {len(filtered)}")
        return filtered
    
    def get_entity_summary(
        self, 
        graph_id: str, 
        entity_name: str
    ) -> Dict[str, Any]:
        """
        Возвращает краткую сводку связей для заданной сущности.
        
        Args:
            graph_id: ID графа
            entity_name: имя сущности
            
        Returns:
            Сводка по сущности
        """
        logger.info(f"Получаю сводку связей для сущности {entity_name}...")
        
        # Сначала ищем связанную с сущностью информацию
        search_result = self.search_graph(
            graph_id=graph_id,
            query=entity_name,
            limit=20
        )
        
        # Пытаемся найти сущность среди всех узлов
        all_nodes = self.get_all_nodes(graph_id)
        entity_node = None
        for node in all_nodes:
            if node.name.lower() == entity_name.lower():
                entity_node = node
                break
        
        related_edges = []
        if entity_node:
            # Добавляем graph_id при чтении ребер
            related_edges = self.get_node_edges(graph_id, entity_node.uuid)
        
        return {
            "entity_name": entity_name,
            "entity_info": entity_node.to_dict() if entity_node else None,
            "related_facts": search_result.facts,
            "related_edges": [e.to_dict() for e in related_edges],
            "total_relations": len(related_edges)
        }
    
    def get_graph_statistics(self, graph_id: str) -> Dict[str, Any]:
        """
        Возвращает статистику графа.
        
        Args:
            graph_id: ID графа
            
        Returns:
            Статистика
        """
        logger.info(f"Получаю статистику графа {graph_id}...")
        
        nodes = self.get_all_nodes(graph_id)
        edges = self.get_all_edges(graph_id)
        
        # Считаем распределение типов сущностей
        entity_types = {}
        for node in nodes:
            for label in node.labels:
                if label not in ["Entity", "Node"]:
                    entity_types[label] = entity_types.get(label, 0) + 1
        
        # Считаем распределение типов связей
        relation_types = {}
        for edge in edges:
            relation_types[edge.name] = relation_types.get(edge.name, 0) + 1
        
        return {
            "graph_id": graph_id,
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "entity_types": entity_types,
            "relation_types": relation_types
        }
    
    def get_simulation_context(
        self, 
        graph_id: str,
        simulation_requirement: str,
        limit: int = 30
    ) -> Dict[str, Any]:
        """
        Возвращает контекст, связанный со сценарием симуляции.
        
        Args:
            graph_id: ID графа
            simulation_requirement: описание сценария
            limit: лимит элементов по категории
            
        Returns:
            Контекст симуляции
        """
        logger.info(f"Получаю контекст симуляции: {simulation_requirement[:50]}...")
        
        # Ищем информацию, связанную со сценарием
        search_result = self.search_graph(
            graph_id=graph_id,
            query=simulation_requirement,
            limit=limit
        )
        
        # Получаем статистику графа
        stats = self.get_graph_statistics(graph_id)
        
        # Получаем все сущности
        all_nodes = self.get_all_nodes(graph_id)
        
        # Отбираем сущности с реальным типом
        entities = []
        for node in all_nodes:
            custom_labels = [l for l in node.labels if l not in ["Entity", "Node"]]
            if custom_labels:
                entities.append({
                    "name": node.name,
                    "type": custom_labels[0],
                    "summary": node.summary
                })
        
        return {
            "simulation_requirement": simulation_requirement,
            "related_facts": search_result.facts,
            "graph_statistics": stats,
            "entities": entities[:limit],
            "total_entities": len(entities)
        }
    
    # ========== Основные инструменты поиска ==========
    
    def insight_forge(
        self,
        graph_id: str,
        query: str,
        simulation_requirement: str,
        report_context: str = "",
        max_sub_queries: int = 5
    ) -> InsightForgeResult:
        """Выполняет глубокий многослойный анализ через InsightForge."""
        logger.info(f"InsightForge: глубокий анализ запроса: {query[:50]}...")
        
        result = InsightForgeResult(
            query=query,
            simulation_requirement=simulation_requirement,
            sub_queries=[]
        )
        
        # Шаг 1: генерируем подвопросы через LLM
        sub_queries = self._generate_sub_queries(
            query=query,
            simulation_requirement=simulation_requirement,
            report_context=report_context,
            max_queries=max_sub_queries
        )
        result.sub_queries = sub_queries
        logger.info(f"Сгенерировано подвопросов: {len(sub_queries)}")
        
        # Шаг 2: выполняем семантический поиск по каждому подвопросу
        all_facts = []
        all_edges = []
        seen_facts = set()
        
        for sub_query in sub_queries:
            search_result = self.search_graph(
                graph_id=graph_id,
                query=sub_query,
                limit=15,
                scope="edges"
            )
            
            for fact in search_result.facts:
                if fact not in seen_facts:
                    all_facts.append(fact)
                    seen_facts.add(fact)
            
            all_edges.extend(search_result.edges)
        
        # Дополнительно ищем по исходному запросу
        main_search = self.search_graph(
            graph_id=graph_id,
            query=query,
            limit=20,
            scope="edges"
        )
        for fact in main_search.facts:
            if fact not in seen_facts:
                all_facts.append(fact)
                seen_facts.add(fact)
        
        result.semantic_facts = all_facts
        result.total_facts = len(all_facts)
        
        # Шаг 3: извлекаем UUID релевантных сущностей из ребер
        entity_uuids = set()
        for edge_data in all_edges:
            if isinstance(edge_data, dict):
                source_uuid = edge_data.get('source_node_uuid', '')
                target_uuid = edge_data.get('target_node_uuid', '')
                if source_uuid:
                    entity_uuids.add(source_uuid)
                if target_uuid:
                    entity_uuids.add(target_uuid)
        
        # Получаем детали всех релевантных сущностей
        entity_insights = []
        node_map = {}
        
        for uuid in list(entity_uuids):
            if not uuid:
                continue
            try:
                # Загружаем каждую связанную сущность отдельно
                node = self.get_node_detail(uuid)
                if node:
                    node_map[uuid] = node
                    entity_type = next((l for l in node.labels if l not in ["Entity", "Node"]), "Сущность")
                    
                    # Собираем все факты, связанные с сущностью
                    related_facts = [
                        f for f in all_facts 
                        if node.name.lower() in f.lower()
                    ]
                    
                    entity_insights.append({
                        "uuid": node.uuid,
                        "name": node.name,
                        "type": entity_type,
                        "summary": node.summary,
                        "related_facts": related_facts
                    })
            except Exception as e:
                logger.debug(f"Не удалось получить узел {uuid}: {e}")
                continue
        
        result.entity_insights = entity_insights
        result.total_entities = len(entity_insights)
        
        # Шаг 4: строим цепочки связей
        relationship_chains = []
        for edge_data in all_edges:
            if isinstance(edge_data, dict):
                source_uuid = edge_data.get('source_node_uuid', '')
                target_uuid = edge_data.get('target_node_uuid', '')
                relation_name = edge_data.get('name', '')
                
                source_name = node_map.get(source_uuid, NodeInfo('', '', [], '', {})).name or source_uuid[:8]
                target_name = node_map.get(target_uuid, NodeInfo('', '', [], '', {})).name or target_uuid[:8]
                
                chain = f"{source_name} --[{relation_name}]--> {target_name}"
                if chain not in relationship_chains:
                    relationship_chains.append(chain)
        
        result.relationship_chains = relationship_chains
        result.total_relationships = len(relationship_chains)
        
        logger.info(f"InsightForge завершен: фактов={result.total_facts}, сущностей={result.total_entities}, связей={result.total_relationships}")
        return result
    
    def _generate_sub_queries(
        self,
        query: str,
        simulation_requirement: str,
        report_context: str = "",
        max_queries: int = 5
    ) -> List[str]:
        """Генерирует подвопросы через LLM."""
        system_prompt = """Ты аналитик исследовательских вопросов. Разбей сложный вопрос на несколько подвопросов, которые можно отдельно проверить внутри симуляции.

Требования:
1. Каждый подвопрос должен быть конкретным и наблюдаемым через поведение агентов, события или связи.
2. Подвопросы должны покрывать разные измерения исходного вопроса: кто, что, почему, как, когда, где.
3. Подвопросы должны быть релевантны сценарию симуляции.
4. Верни JSON в формате {"sub_queries": ["подвопрос 1", "подвопрос 2", ...]}"""

        user_prompt = f"""Контекст симуляции:
{simulation_requirement}

{f"Контекст раздела отчета: {report_context[:500]}" if report_context else ""}

Разбей следующий вопрос на {max_queries} подвопросов:
{query}

Верни только JSON со списком подвопросов."""

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3
            )
            
            sub_queries = response.get("sub_queries", [])
            # Гарантируем список строк
            return [str(sq) for sq in sub_queries[:max_queries]]
            
        except Exception as e:
            logger.warning(f"Не удалось сгенерировать подвопросы: {str(e)}. Использую резервный набор.")
            # Запасной набор подвопросов
            return [
                query,
                f"Ключевые участники вопроса: {query}",
                f"Причины и последствия вопроса: {query}",
                f"Динамика развития вопроса: {query}"
            ][:max_queries]
    
    def panorama_search(
        self,
        graph_id: str,
        query: str,
        include_expired: bool = True,
        limit: int = 50
    ) -> PanoramaResult:
        """Выполняет панорамный поиск PanoramaSearch."""
        logger.info(f"PanoramaSearch: обзорный поиск по запросу: {query[:50]}...")
        
        result = PanoramaResult(query=query)
        
        # Получаем все узлы
        all_nodes = self.get_all_nodes(graph_id)
        node_map = {n.uuid: n for n in all_nodes}
        result.all_nodes = all_nodes
        result.total_nodes = len(all_nodes)
        
        # Получаем все ребра с временными полями
        all_edges = self.get_all_edges(graph_id, include_temporal=True)
        result.all_edges = all_edges
        result.total_edges = len(all_edges)
        
        # Разделяем факты на активные и исторические
        active_facts = []
        historical_facts = []
        
        for edge in all_edges:
            if not edge.fact:
                continue
            
            # Подтягиваем названия сущностей
            source_name = node_map.get(edge.source_node_uuid, NodeInfo('', '', [], '', {})).name or edge.source_node_uuid[:8]
            target_name = node_map.get(edge.target_node_uuid, NodeInfo('', '', [], '', {})).name or edge.target_node_uuid[:8]
            
            # Определяем, является ли факт историческим
            is_historical = edge.is_expired or edge.is_invalid
            
            if is_historical:
                # Исторический или истекший факт
                valid_at = edge.valid_at or "Неизвестно"
                invalid_at = edge.invalid_at or edge.expired_at or "Неизвестно"
                fact_with_time = f"[{valid_at} - {invalid_at}] {edge.fact}"
                historical_facts.append(fact_with_time)
            else:
                # Актуальный факт
                active_facts.append(edge.fact)
        
        # Сортируем по релевантности запросу
        query_lower = query.lower()
        keywords = [w.strip() for w in query_lower.replace(',', ' ').replace('，', ' ').split() if len(w.strip()) > 1]
        
        def relevance_score(fact: str) -> int:
            fact_lower = fact.lower()
            score = 0
            if query_lower in fact_lower:
                score += 100
            for kw in keywords:
                if kw in fact_lower:
                    score += 10
            return score
        
        # Ограничиваем выдачу по лимиту
        active_facts.sort(key=relevance_score, reverse=True)
        historical_facts.sort(key=relevance_score, reverse=True)
        
        result.active_facts = active_facts[:limit]
        result.historical_facts = historical_facts[:limit] if include_expired else []
        result.active_count = len(active_facts)
        result.historical_count = len(historical_facts)
        
        logger.info(f"PanoramaSearch завершен: активных={result.active_count}, исторических={result.historical_count}")
        return result
    
    def quick_search(
        self,
        graph_id: str,
        query: str,
        limit: int = 10
    ) -> SearchResult:
        """Выполняет быстрый облегченный поиск QuickSearch."""
        logger.info(f"QuickSearch: быстрый поиск по запросу: {query[:50]}...")
        
        # Используем существующий search_graph напрямую
        result = self.search_graph(
            graph_id=graph_id,
            query=query,
            limit=limit,
            scope="edges"
        )
        
        logger.info(f"QuickSearch завершен: результатов={result.total_count}")
        return result
    
    def interview_agents(
        self,
        simulation_id: str,
        interview_requirement: str,
        simulation_requirement: str = "",
        max_agents: int = 5,
        custom_questions: List[str] = None
    ) -> InterviewResult:
        """Проводит глубокие интервью с агентами через реальный API симуляции."""
        from .simulation_runner import SimulationRunner
        
        logger.info(f"InterviewAgents: запускаю глубинные интервью через реальный API: {interview_requirement[:50]}...")
        
        result = InterviewResult(
            interview_topic=interview_requirement,
            interview_questions=custom_questions or []
        )
        
        # Шаг 1: загружаем профили
        profiles = self._load_agent_profiles(simulation_id)
        
        if not profiles:
            logger.warning(f"Файл профилей для симуляции {simulation_id} не найден")
            result.summary = "Не удалось найти профили агентов для интервью"
            return result
        
        result.total_agents = len(profiles)
        logger.info(f"Загружено профилей агентов: {len(profiles)}")
        
        # Шаг 2: выбираем агентов через LLM
        selected_agents, selected_indices, selection_reasoning = self._select_agents_for_interview(
            profiles=profiles,
            interview_requirement=interview_requirement,
            simulation_requirement=simulation_requirement,
            max_agents=max_agents
        )
        
        result.selected_agents = selected_agents
        result.selection_reasoning = selection_reasoning
        logger.info(f"Для интервью выбрано агентов: {len(selected_agents)}; индексы: {selected_indices}")
        
        # Шаг 3: генерируем вопросы, если их не передали вручную
        if not result.interview_questions:
            result.interview_questions = self._generate_interview_questions(
                interview_requirement=interview_requirement,
                simulation_requirement=simulation_requirement,
                selected_agents=selected_agents
            )
            logger.info(f"Сгенерировано вопросов для интервью: {len(result.interview_questions)}")
        
        # Объединяем вопросы в один prompt
        combined_prompt = "\n".join([f"{i+1}. {q}" for i, q in enumerate(result.interview_questions)])
        
        # Добавляем префикс, чтобы агент отвечал обычным текстом
        INTERVIEW_PROMPT_PREFIX = (
            "Ты отвечаешь на интервью. Опирайся на свой профиль, память и прошлые действия, "
            "и отвечай напрямую обычным текстом.\n"
            "Требования к ответу:\n"
            "1. Отвечай естественным языком, не вызывай инструменты\n"
            "2. Не возвращай JSON и не используй формат вызова инструментов\n"
            "3. Не используй Markdown-заголовки вроде #, ##, ###\n"
            "4. Отвечай на вопросы по порядку, каждый ответ начинай с «Вопрос X:»\n"
            "5. Разделяй ответы пустой строкой\n"
            "6. В каждом ответе должно быть минимум 2-3 содержательных предложения\n\n"
        )
        optimized_prompt = f"{INTERVIEW_PROMPT_PREFIX}{combined_prompt}"
        
        # Шаг 4: вызываем реальный API интервью
        try:
            # Собираем запрос для пакетного интервью на двух платформах
            interviews_request = []
            for agent_idx in selected_indices:
                interviews_request.append({
                    "agent_id": agent_idx,
                    "prompt": optimized_prompt
                })
            
            logger.info(f"Вызываю batch-interview API для двух платформ: агентов={len(interviews_request)}")
            
            # Запускаем пакетное интервью без фиксации платформы
            api_result = SimulationRunner.interview_agents_batch(
                simulation_id=simulation_id,
                interviews=interviews_request,
                platform=None,
                timeout=180.0
            )
            
            logger.info(f"Interview API вернул результатов={api_result.get('interviews_count', 0)}, success={api_result.get('success')}")
            
            # Проверяем результат API
            if not api_result.get("success", False):
                error_msg = api_result.get("error", "Неизвестная ошибка")
                logger.warning(f"Interview API вернул ошибку: {error_msg}")
                result.summary = f"Не удалось выполнить интервью через API: {error_msg}. Проверь состояние среды OASIS."
                return result
            
            # Шаг 5: разбираем ответ API и собираем AgentInterview
            api_data = api_result.get("result", {})
            results_dict = api_data.get("results", {}) if isinstance(api_data, dict) else {}
            
            for i, agent_idx in enumerate(selected_indices):
                agent = selected_agents[i]
                agent_name = agent.get("realname", agent.get("username", f"Agent_{agent_idx}"))
                agent_role = agent.get("profession", "Неизвестно")
                agent_bio = agent.get("bio", "")
                
                # Получаем ответы агента на двух платформах
                twitter_result = results_dict.get(f"twitter_{agent_idx}", {})
                reddit_result = results_dict.get(f"reddit_{agent_idx}", {})
                
                twitter_response = twitter_result.get("response", "")
                reddit_response = reddit_result.get("response", "")

                # Удаляем возможную JSON-обертку вызова инструмента
                twitter_response = self._clean_tool_call_response(twitter_response)
                reddit_response = self._clean_tool_call_response(reddit_response)

                # Всегда сохраняем явную разметку по платформам
                twitter_text = twitter_response if twitter_response else "Ответ на этой платформе не получен"
                reddit_text = reddit_response if reddit_response else "Ответ на этой платформе не получен"
                response_text = f"[Ответ Twitter]\n{twitter_text}\n\n[Ответ Reddit]\n{reddit_text}"

                # Извлекаем ключевые цитаты
                import re
                combined_responses = f"{twitter_response} {reddit_response}"

                # Очищаем текст ответа от служебной разметки
                clean_text = re.sub(r'#{1,6}\s+', '', combined_responses)
                clean_text = re.sub(r'\{[^}]*tool_name[^}]*\}', '', clean_text)
                clean_text = re.sub(r'[*_`|>~\-]{2,}', '', clean_text)
                clean_text = re.sub(r'(?:\u95ee\u9898|Вопрос)\d+[：:]\s*', '', clean_text)
                clean_text = re.sub(r'[\[\u3010][^\]\u3011]+[\]\u3011]', '', clean_text)

                # Стратегия 1: полноценные содержательные предложения
                sentences = re.split(r'[\u3002\uff01\uff1f.!?]', clean_text)
                meaningful = [
                    s.strip() for s in sentences
                    if 20 <= len(s.strip()) <= 150
                    and not re.match(r'^[\s\W\uff0c,；;：:\u3001]+', s.strip())
                    and not s.strip().startswith(('{', '\u95ee\u9898', 'Вопрос'))
                ]
                meaningful.sort(key=len, reverse=True)
                key_quotes = [s + "." for s in meaningful[:3]]

                # Стратегия 2: длинные цитаты в парных кавычках
                if not key_quotes:
                    paired = re.findall(r'\u201c([^\u201c\u201d]{15,100})\u201d', clean_text)
                    paired += re.findall(r'\u300c([^\u300c\u300d]{15,100})\u300d', clean_text)
                    key_quotes = [q for q in paired if not re.match(r'^[\uff0c,；;：:\u3001]', q)][:3]
                
                interview = AgentInterview(
                    agent_name=agent_name,
                    agent_role=agent_role,
                    agent_bio=agent_bio[:1000],
                    question=combined_prompt,
                    response=response_text,
                    key_quotes=key_quotes[:5]
                )
                result.interviews.append(interview)
            
            result.interviewed_count = len(result.interviews)
            
        except ValueError as e:
            # Среда симуляции не запущена
            logger.warning(f"Ошибка вызова interview API, возможно среда не запущена: {e}")
            result.summary = f"Интервью завершилось ошибкой: {str(e)}. Возможно, среда симуляции уже закрыта. Проверь, что OASIS запущен."
            return result
        except Exception as e:
            logger.error(f"Исключение при вызове interview API: {e}")
            import traceback
            logger.error(traceback.format_exc())
            result.summary = f"Во время интервью произошла ошибка: {str(e)}"
            return result
        
        # Шаг 6: генерируем итоговое резюме интервью
        if result.interviews:
            result.summary = self._generate_interview_summary(
                interviews=result.interviews,
                interview_requirement=interview_requirement
            )
        
        logger.info(f"InterviewAgents завершен: опрошено агентов={result.interviewed_count} на двух платформах")
        return result
    
    @staticmethod
    def _clean_tool_call_response(response: str) -> str:
        """Очищает ответ агента от JSON-обертки вызова инструмента."""
        if not response or not response.strip().startswith('{'):
            return response
        text = response.strip()
        if 'tool_name' not in text[:80]:
            return response
        import re as _re
        try:
            data = json.loads(text)
            if isinstance(data, dict) and 'arguments' in data:
                for key in ('content', 'text', 'body', 'message', 'reply'):
                    if key in data['arguments']:
                        return str(data['arguments'][key])
        except (json.JSONDecodeError, KeyError, TypeError):
            match = _re.search(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
            if match:
                return match.group(1).replace('\\n', '\n').replace('\\"', '"')
        return response

    def _load_agent_profiles(self, simulation_id: str) -> List[Dict[str, Any]]:
        """Загружает файлы профилей агентов для симуляции."""
        import os
        import csv
        
        # Собираем путь к файлам профилей
        sim_dir = os.path.join(
            os.path.dirname(__file__), 
            f'../../uploads/simulations/{simulation_id}'
        )
        
        profiles = []
        
        # Сначала пробуем Reddit JSON
        reddit_profile_path = os.path.join(sim_dir, "reddit_profiles.json")
        if os.path.exists(reddit_profile_path):
            try:
                with open(reddit_profile_path, 'r', encoding='utf-8') as f:
                    profiles = json.load(f)
                logger.info(f"Из reddit_profiles.json загружено профилей: {len(profiles)}")
                return profiles
            except Exception as e:
                logger.warning(f"Не удалось прочитать reddit_profiles.json: {e}")
        
        # Затем пробуем Twitter CSV
        twitter_profile_path = os.path.join(sim_dir, "twitter_profiles.csv")
        if os.path.exists(twitter_profile_path):
            try:
                with open(twitter_profile_path, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        # Приводим CSV к единому формату
                        profiles.append({
                            "realname": row.get("name", ""),
                            "username": row.get("username", ""),
                            "bio": row.get("description", ""),
                            "persona": row.get("user_char", ""),
                            "profession": "Неизвестно"
                        })
                logger.info(f"Из twitter_profiles.csv загружено профилей: {len(profiles)}")
                return profiles
            except Exception as e:
                logger.warning(f"Не удалось прочитать twitter_profiles.csv: {e}")
        
        return profiles
    
    def _select_agents_for_interview(
        self,
        profiles: List[Dict[str, Any]],
        interview_requirement: str,
        simulation_requirement: str,
        max_agents: int
    ) -> tuple:
        """
        Выбирает агентов для интервью через LLM.
        """
        
        # Строим краткие профили агентов
        agent_summaries = []
        for i, profile in enumerate(profiles):
            summary = {
                "index": i,
                "name": profile.get("realname", profile.get("username", f"Agent_{i}")),
                "profession": profile.get("profession", "Неизвестно"),
                "bio": profile.get("bio", "")[:200],
                "interested_topics": profile.get("interested_topics", [])
            }
            agent_summaries.append(summary)
        
        system_prompt = """Ты редактор интервью. По описанию задачи выбери из списка агентов тех, кого стоит опросить в первую очередь.

Критерии выбора:
1. Роль или профессия агента связана с темой интервью.
2. Агент потенциально дает уникальную или полезную точку зрения.
3. Нужны разные перспективы: поддержка, критика, нейтральная позиция, экспертность и т.д.
4. Приоритет у агентов, напрямую связанных с событием.

Верни JSON:
{
    "selected_indices": [индексы выбранных агентов],
    "reasoning": "краткое объяснение выбора"
}"""

        user_prompt = f"""Задача интервью:
{interview_requirement}

Контекст симуляции:
{simulation_requirement if simulation_requirement else "не указан"}

Доступные агенты ({len(agent_summaries)}):
{json.dumps(agent_summaries, ensure_ascii=False, indent=2)}

Выбери не более {max_agents} наиболее подходящих агентов и объясни выбор."""

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3
            )
            
            selected_indices = response.get("selected_indices", [])[:max_agents]
            reasoning = response.get("reasoning", "Автоматический выбор по релевантности")
            
            # Собираем полные данные выбранных агентов
            selected_agents = []
            valid_indices = []
            for idx in selected_indices:
                if 0 <= idx < len(profiles):
                    selected_agents.append(profiles[idx])
                    valid_indices.append(idx)
            
            return selected_agents, valid_indices, reasoning
            
        except Exception as e:
            logger.warning(f"Не удалось выбрать агентов через LLM, использую выбор по умолчанию: {e}")
            # Резервный сценарий: берем первые N агентов
            selected = profiles[:max_agents]
            indices = list(range(min(max_agents, len(profiles))))
            return selected, indices, "Использована стратегия выбора по умолчанию"
    
    def _generate_interview_questions(
        self,
        interview_requirement: str,
        simulation_requirement: str,
        selected_agents: List[Dict[str, Any]]
    ) -> List[str]:
        """Генерирует вопросы для интервью через LLM."""
        
        agent_roles = [a.get("profession", "Неизвестно") for a in selected_agents]
        
        system_prompt = """Ты журналист и интервьюер. Сгенерируй 3-5 глубоких вопросов для интервью.

Требования к вопросам:
1. Вопросы должны быть открытыми и побуждать к развернутому ответу.
2. Они должны позволять разным ролям отвечать по-разному.
3. Нужно покрыть факты, мнения и чувства.
4. Формулировки должны звучать естественно, как в реальном интервью.
5. Каждый вопрос должен быть коротким и ясным.
6. Задавай вопросы напрямую, без длинных вступлений.

Верни JSON: {"questions": ["вопрос 1", "вопрос 2", ...]}"""

        user_prompt = f"""Задача интервью: {interview_requirement}

Контекст симуляции: {simulation_requirement if simulation_requirement else "не указан"}

Роли выбранных собеседников: {', '.join(agent_roles)}

Сформируй 3-5 вопросов для интервью."""

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.5
            )
            
            return response.get("questions", [f"Каково ваше мнение по поводу: {interview_requirement}?"])
            
        except Exception as e:
            logger.warning(f"Не удалось сгенерировать вопросы для интервью: {e}")
            return [
                f"Какова ваша позиция по поводу: {interview_requirement}?",
                "Как это событие влияет на вас или на группу, которую вы представляете?",
                "Что, по вашему мнению, нужно изменить или сделать дальше?"
            ]
    
    def _generate_interview_summary(
        self,
        interviews: List[AgentInterview],
        interview_requirement: str
    ) -> str:
        """Генерирует итоговое резюме интервью."""
        
        if not interviews:
            return "Интервью не проводились"
        
        # Собираем тексты интервью
        interview_texts = []
        for interview in interviews:
            interview_texts.append(f"[{interview.agent_name} ({interview.agent_role})]\n{interview.response[:500]}")
        
        system_prompt = """Ты редактор аналитических интервью. По ответам нескольких собеседников собери краткое нейтральное резюме.

Требования:
1. Выдели основные позиции сторон.
2. Покажи, где мнения совпадают, а где расходятся.
3. Отметь наиболее ценные цитаты.
4. Сохраняй нейтральный и аналитический тон.
5. Держи итог в пределах примерно 1000 слов.

Формат:
- обычный текст с абзацами
- без Markdown-заголовков и разделителей
- цитаты можно оформлять в кавычках
- допустимо выделять ключевые слова жирным"""

        user_prompt = f"""Тема интервью: {interview_requirement}

Содержимое интервью:
{"".join(interview_texts)}

Собери краткое аналитическое резюме интервью."""

        try:
            summary = self.llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3,
                max_tokens=800
            )
            return summary
            
        except Exception as e:
            logger.warning(f"Не удалось сгенерировать итог интервью: {e}")
            # 降级：简单拼接
            return f"Опрошено участников: {len(interviews)}. В их числе: " + ", ".join([i.agent_name for i in interviews])
