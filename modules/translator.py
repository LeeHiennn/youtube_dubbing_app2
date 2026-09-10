import time
import random
import re
from deep_translator import GoogleTranslator

VN_CHARS = re.compile(r'[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]', re.IGNORECASE)
ZH_CHARS = re.compile('[\u4e00-\u9fff]')

def contains_vietnamese(text):
    """Kiểm tra xem text có chứa ký tự tiếng Việt có dấu hay không."""
    return bool(VN_CHARS.search(text))

def detect_source(text):
    """Tự động phát hiện ngôn ngữ nguồn (Hỗ trợ Trung, Việt, Anh)."""
    if ZH_CHARS.search(text):
        return 'zh-CN'
    if contains_vietnamese(text):
        return 'vi'
    return 'en'

def translate_segments(segments, source_lang="auto", target_lang="vi"):
    """
    Dịch các đoạn văn bản sang ngôn ngữ đích.
    Có cache kết quả, kiểm tra ngôn ngữ, và tự động retry (exponential backoff) nếu gặp lỗi.
    Hỗ trợ checkpoint: segment có 'translation_ok'=True sẽ được reuse,
    segment lỗi (False) hoặc legacy (translated_text==text) sẽ được retry.
    """
    print(f"Đang dịch thuật các đoạn văn bản (target: {target_lang})...")
    
    translation_cache = {}
    translators = {}
    
    count_ok = 0
    count_reused = 0
    count_failed = 0
    consecutive_failures = 0
    
    for i, segment in enumerate(segments):
        original_text = segment['text']
        
        # Bỏ qua nếu text rỗng
        if not original_text.strip():
            segment['translated_text'] = ""
            segment['source_lang'] = "unknown"
            segment['translation_ok'] = True
            count_ok += 1
            continue
            
        # Xác định ngôn ngữ nguồn
        actual_source = detect_source(original_text) if source_lang == "auto" else source_lang
        if actual_source == "zh":
            actual_source = "zh-CN"
        segment['source_lang'] = actual_source
        
        # Với tiếng Việt, giữ nguyên hành vi cũ (không dịch, copy text)
        if actual_source == 'vi':
            segment['translated_text'] = original_text
            segment['translation_ok'] = True
            print(f"[{i+1}/{len(segments)}] Bỏ qua dịch (source={actual_source}): {original_text[:30]}...")
            count_ok += 1
            continue
        
        # --- Checkpoint-aware reuse / retry ---
        if 'translation_ok' in segment:
            if segment['translation_ok'] and segment.get('translated_text'):
                # Đã dịch OK từ checkpoint trước -> reuse
                translation_cache[original_text] = segment['translated_text']
                print(f"[{i+1}/{len(segments)}] Reuse từ checkpoint (source={actual_source}): {segment['translated_text'][:30]}...")
                count_reused += 1
                continue
            else:
                # translation_ok=False -> segment lỗi từ lần trước, cần retry
                print(f"[{i+1}/{len(segments)}] Retry segment lỗi từ checkpoint trước...")
        else:
            # Legacy data (không có cờ): phát hiện lỗi bằng heuristic
            if segment.get('translated_text') and segment['translated_text'] != original_text:
                # Có bản dịch khác text gốc -> coi là OK
                segment['translation_ok'] = True
                translation_cache[original_text] = segment['translated_text']
                print(f"[{i+1}/{len(segments)}] Reuse legacy (source={actual_source}): {segment['translated_text'][:30]}...")
                count_reused += 1
                continue
            elif segment.get('translated_text') and segment['translated_text'] == original_text:
                # translated_text == text gốc và không phải vi -> fallback lỗi
                print(f"[{i+1}/{len(segments)}] Phát hiện legacy lỗi (text==translated_text), retry...")
            # Không có translated_text -> segment mới, cần dịch
            
        # Kiểm tra in-memory cache
        if original_text in translation_cache:
            segment['translated_text'] = translation_cache[original_text]
            segment['translation_ok'] = True
            print(f"[{i+1}/{len(segments)}] Dịch từ cache (source={actual_source}): {segment['translated_text'][:30]}...")
            count_ok += 1
            continue
            
        # Thử dịch API với Retry logic (tối đa 3 lần)
        # GoogleTranslator init + translate đều trong try để không crash pipeline
        success = False
        for attempt in range(3):
            try:
                if actual_source not in translators:
                    translators[actual_source] = GoogleTranslator(source=actual_source, target=target_lang)
                translator = translators[actual_source]
                
                translation = translator.translate(original_text)
                segment['translated_text'] = translation
                segment['translation_ok'] = True
                translation_cache[original_text] = translation
                
                print(f"[{i+1}/{len(segments)}] Dịch thành công (source={actual_source}): {translation[:30]}...")
                success = True
                count_ok += 1
                consecutive_failures = 0
                
                # Nghỉ nhẹ giữa các lần dịch thành công
                time.sleep(random.uniform(0.15, 0.25))
                break
                
            except Exception as e:
                consecutive_failures += 1
                wait_time = min(60, (2 ** consecutive_failures) * 5 + random.uniform(0, 2))
                print(f"Lỗi khi dịch đoạn {i+1} (lần {attempt+1}, streak={consecutive_failures}): {e}. Nghỉ {wait_time:.1f}s...")
                # Xóa translator cache để tạo lại instance mới
                translators.pop(actual_source, None)
                time.sleep(wait_time)
                
        if not success:
            print(f"[{i+1}/{len(segments)}] Dịch thất bại sau 3 lần thử, giữ nguyên gốc.")
            segment['translated_text'] = original_text
            segment['translation_ok'] = False
            count_failed += 1
            # Nếu đang bị rate-limit nặng, nghỉ dài trước khi thử segment tiếp
            if consecutive_failures >= 2:
                cooldown = min(60, consecutive_failures * 10 + random.uniform(0, 3))
                print(f"⏳ Rate-limit detected (streak={consecutive_failures}), cooldown {cooldown:.1f}s trước segment tiếp...")
                time.sleep(cooldown)
    
    # Tổng kết
    total = len(segments)
    print(f"Dịch thuật hoàn tất! Đã dịch {count_ok}/{total}, reuse {count_reused}, lỗi {count_failed}")
    if count_failed > 0:
        print(f"⚠️ {count_failed} đoạn dịch lỗi sẽ được retry ở lần chạy kế tiếp.")
    return segments
