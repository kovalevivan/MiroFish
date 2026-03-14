"""Сервис обработки текста."""

from typing import List, Optional
from ..utils.file_parser import FileParser, split_text_into_chunks


class TextProcessor:
    """Утилиты для извлечения и предварительной обработки текста."""
    
    @staticmethod
    def extract_from_files(file_paths: List[str]) -> str:
        """Извлекает текст из нескольких файлов."""
        return FileParser.extract_from_multiple(file_paths)
    
    @staticmethod
    def split_text(
        text: str,
        chunk_size: int = 500,
        overlap: int = 50
    ) -> List[str]:
        """Разбивает текст на чанки."""
        return split_text_into_chunks(text, chunk_size, overlap)
    
    @staticmethod
    def preprocess_text(text: str) -> str:
        """
        Выполняет предобработку текста.
        - убирает лишние пробелы;
        - нормализует переводы строк.
        """
        import re
        
        # Нормализуем переводы строк
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        
        # Схлопываем слишком длинные последовательности пустых строк
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        # Обрезаем пробелы по краям строк
        lines = [line.strip() for line in text.split('\n')]
        text = '\n'.join(lines)
        
        return text.strip()
    
    @staticmethod
    def get_text_stats(text: str) -> dict:
        """Возвращает базовую статистику по тексту."""
        return {
            "total_chars": len(text),
            "total_lines": text.count('\n') + 1,
            "total_words": len(text.split()),
        }
