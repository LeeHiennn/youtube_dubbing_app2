import time
import random
import re
from deep_translator import GoogleTranslator

VN_CHARS = re.compile(r'[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]', re.IGNORECASE)
ZH_CHARS = re.compile('[\u4e00-\u9fff]')

BATCH_DELIMITER = "\n|||SPLIT|||\n"
BATCH_SIZE = 25

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

def _translate_batch(texts, source_lang, target_lang, translators):
    """
    Dịch một batch texts cùng lúc bằng cách gom lại thành 1 request duy nhất.
    Trả về list kết quả dịch tương ứng, hoặc None nếu delimiter bị mất.
    """
    if not texts:
        return []
    
    combined = BATCH_DELIMITER.join(texts)
    
    if source_lang not in translators:
        translators[source_lang] = GoogleTranslator(source=source_lang, target=target_lang)
    translator = translators[source_lang]
    
    translated_combined = translator.translate(combined)
    
    # Tách kết quả theo delimiter
    parts = translated_combined.split("|||SPLIT|||")
    
    # Nếu số phần tách ra khớp với số input -> OK
    if len(parts) == len(texts):
        return [p.strip() for p in parts]
    
    # Delimiter bị dịch/mất -> trả None để fallback
    return None

def translate_segments(segments, source_lang="auto", target_lang="vi"):
    """
    Dịch các đoạn văn bản sang ngôn ngữ đích.
    Sử dụng batch translation để giảm số HTTP request (25 segments/batch).
    Có cache kết quả, kiểm tra ngôn ngữ, và tự động retry nếu gặp lỗi.
    Hỗ trợ checkpoint: segment có 'translation_ok'=True sẽ được reuse.
    """
    print(f"Đang dịch thuật các đoạn văn bản (target: {target_lang})...")
    
    translation_cache = {}
    translators = {}
    
    count_ok = 0
    count_reused = 0
    count_failed = 0
    
    # === Phase 1: Phân loại segments (reuse / skip / cần dịch) ===
    needs_translation = []  # list of (index, source_lang) cần dịch mới
    
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
        
        # Với tiếng Việt, giữ nguyên (không dịch)
        if actual_source == 'vi':
            segment['translated_text'] = original_text
            segment['translation_ok'] = True
            print(f"[{i+1}/{len(segments)}] Bỏ qua dịch (source={actual_source}): {original_text[:30]}...")
            count_ok += 1
            continue
        
        # --- Checkpoint-aware reuse / retry ---
        if 'translation_ok' in segment:
            if segment['translation_ok'] and segment.get('translated_text'):
                translation_cache[original_text] = segment['translated_text']
                count_reused += 1
                continue
            else:
                pass  # translation_ok=False -> cần retry
        else:
            # Legacy data: phát hiện lỗi bằng heuristic
            if segment.get('translated_text') and segment['translated_text'] != original_text:
                segment['translation_ok'] = True
                translation_cache[original_text] = segment['translated_text']
                count_reused += 1
                continue
            elif segment.get('translated_text') and segment['translated_text'] == original_text:
                pass  # fallback lỗi, cần retry
        
        # Kiểm tra in-memory cache
        if original_text in translation_cache:
            segment['translated_text'] = translation_cache[original_text]
            segment['translation_ok'] = True
            count_ok += 1
            continue
        
        # Cần dịch mới
        needs_translation.append((i, actual_source))
    
    if count_reused > 0:
        print(f"Đã reuse {count_reused} segment từ checkpoint/cache.")
    
    if not needs_translation:
        print(f"Dịch thuật hoàn tất! Tất cả {len(segments)} segment đã có sẵn bản dịch.")
        return segments
    
    print(f"Cần dịch mới {len(needs_translation)} segments (batch size={BATCH_SIZE})...")
    
    # === Phase 2: Gom batch theo source_lang và dịch ===
    by_lang = {}
    for idx, src_lang in needs_translation:
        by_lang.setdefault(src_lang, []).append(idx)
    
    consecutive_failures = 0
    
    for src_lang, indices in by_lang.items():
        # Chia thành các batch
        for batch_start in range(0, len(indices), BATCH_SIZE):
            batch_indices = indices[batch_start:batch_start + BATCH_SIZE]
            batch_texts = [segments[idx]['text'] for idx in batch_indices]
            batch_num = batch_start // BATCH_SIZE + 1
            total_batches = (len(indices) + BATCH_SIZE - 1) // BATCH_SIZE
            
            print(f"[Batch {batch_num}/{total_batches}] Dịch {len(batch_texts)} segments ({src_lang} → {target_lang})...")
            
            # Thử batch translation (tối đa 3 lần)
            batch_success = False
            for attempt in range(3):
                try:
                    results = _translate_batch(batch_texts, src_lang, target_lang, translators)
                    
                    if results is not None:
                        for idx, translation in zip(batch_indices, results):
                            segments[idx]['translated_text'] = translation
                            segments[idx]['translation_ok'] = True
                            translation_cache[segments[idx]['text']] = translation
                        count_ok += len(batch_indices)
                        consecutive_failures = 0
                        batch_success = True
                        print(f"[Batch {batch_num}/{total_batches}] ✅ Thành công ({len(batch_texts)} segments)")
                        time.sleep(random.uniform(0.3, 0.6))
                        break
                    else:
                        print(f"[Batch {batch_num}] Delimiter bị mất, chuyển sang dịch từng segment...")
                        raise ValueError("Batch delimiter lost")
                        
                except Exception as e:
                    consecutive_failures += 1
                    if attempt < 2:
                        wait_time = min(30, (2 ** attempt) * 3 + random.uniform(0, 2))
                        print(f"Batch lỗi (lần {attempt+1}): {e}. Nghỉ {wait_time:.1f}s...")
                        translators.pop(src_lang, None)
                        time.sleep(wait_time)
            
            if not batch_success:
                # Fallback: dịch từng segment riêng lẻ
                print(f"Batch thất bại, fallback dịch từng segment...")
                for idx in batch_indices:
                    text = segments[idx]['text']
                    
                    if text in translation_cache:
                        segments[idx]['translated_text'] = translation_cache[text]
                        segments[idx]['translation_ok'] = True
                        count_ok += 1
                        continue
                    
                    seg_success = False
                    for attempt in range(3):
                        try:
                            if src_lang not in translators:
                                translators[src_lang] = GoogleTranslator(source=src_lang, target=target_lang)
                            translation = translators[src_lang].translate(text)
                            segments[idx]['translated_text'] = translation
                            segments[idx]['translation_ok'] = True
                            translation_cache[text] = translation
                            count_ok += 1
                            consecutive_failures = 0
                            seg_success = True
                            time.sleep(random.uniform(0.1, 0.2))
                            break
                        except Exception as e2:
                            consecutive_failures += 1
                            wait_time = min(60, (2 ** consecutive_failures) * 5 + random.uniform(0, 2))
                            print(f"Lỗi dịch segment {idx+1} (lần {attempt+1}): {e2}. Nghỉ {wait_time:.1f}s...")
                            translators.pop(src_lang, None)
                            time.sleep(wait_time)
                    
                    if not seg_success:
                        segments[idx]['translated_text'] = text
                        segments[idx]['translation_ok'] = False
                        count_failed += 1
                        if consecutive_failures >= 2:
                            cooldown = min(60, consecutive_failures * 10 + random.uniform(0, 3))
                            print(f"⏳ Rate-limit detected, cooldown {cooldown:.1f}s...")
                            time.sleep(cooldown)
    
    # Tổng kết
    total = len(segments)
    print(f"Dịch thuật hoàn tất! Đã dịch {count_ok}/{total}, reuse {count_reused}, lỗi {count_failed}")
    if count_failed > 0:
        print(f"⚠️ {count_failed} đoạn dịch lỗi sẽ được retry ở lần chạy kế tiếp.")
    return segments
