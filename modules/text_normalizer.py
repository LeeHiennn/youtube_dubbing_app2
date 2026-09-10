import re
from modules.pronunciation_dict import (
    SCIENTIST_NAMES, TECH_TERMS, GREEK_LETTERS, COMMON_UNITS, MATH_PATTERNS
)

def normalize_for_tts(text):
    """
    Chuẩn hóa text trước khi gửi vào TTS:
    1. Thay thế tên nhà khoa học -> SSML English
    2. Thay thế từ kỹ thuật -> phiên âm Việt hoặc SSML
    3. Thay thế Greek letters -> tên tiếng Anh
    4. Thay thế đơn vị -> phiên âm Việt
    5. Áp dụng math patterns
    """
    result = text
    
    # Ưu tiên: tên nhà khoa học (quan trọng nhất)
    for name, ssml_name in sorted(SCIENTIST_NAMES.items(), key=lambda x: -len(x[0])):
        result = result.replace(name, ssml_name)
    
    # Từ kỹ thuật
    for term, replacement in sorted(TECH_TERMS.items(), key=lambda x: -len(x[0])):
        result = result.replace(term, replacement)
    
    # Greek letters
    for letter, name in GREEK_LETTERS.items():
        result = result.replace(letter, name)
    
    # Đơn vị
    for unit, viet in COMMON_UNITS.items():
        result = result.replace(unit, viet)
    
    # Math patterns
    for pattern, replacement in MATH_PATTERNS:
        result = re.sub(pattern, replacement, result)
    
    return result


def wrap_unknown_proper_nouns(text):
    """
    Do edge-tts không hỗ trợ chèn thẻ SSML tự do, hàm này sẽ chỉ trả về text gốc.
    """
    return text
