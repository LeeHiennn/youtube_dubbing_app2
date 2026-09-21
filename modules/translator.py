import time
import random
import re
import requests
from deep_translator import GoogleTranslator, MyMemoryTranslator
from modules.sentence_grouper import group_into_sentences, split_translation_back, compute_source_fractions

VN_CHARS = re.compile(r'[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]', re.IGNORECASE)
ZH_CHARS = re.compile('[\u4e00-\u9fff]')

BATCH_SIZE = 8  # Số câu (sentence) cho mỗi batch request

# Regex tách kết quả dịch theo marker [1], [2], ...
_MARKER_RE = re.compile(r'\[(\d+)\]\s*')

# Từ đệm tiếng Anh thường gặp trong Whisper
_FILLER_RE = re.compile(
    r'\b(?:um|uh|uhm|hmm|hm|ah|eh|er|erm|like|you know|i mean|so|well|okay|ok|right)\b',
    re.IGNORECASE
)
# Dấu lặp
_MULTI_DOT_RE = re.compile(r'\.{2,}')
_MULTI_EXCL_RE = re.compile(r'!{2,}')
_MULTI_QUES_RE = re.compile(r'\?{2,}')
_MULTI_SPACE_RE = re.compile(r'\s{2,}')
# Khoảng trắng trước dấu câu
_SPACE_BEFORE_PUNCT_RE = re.compile(r'\s+([,.:;!?])')
# Thiếu khoảng trắng sau dấu câu (nhưng không phải số thập phân)
_NO_SPACE_AFTER_PUNCT_RE = re.compile(r'([,.;!?])([A-ZĐa-zđàáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵ])')

# Global HTTP Session để tái sử dụng kết nối
_session = requests.Session()
_headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
}


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


def clean_source_text(text):
    """
    Làm sạch text nguồn trước khi dịch:
    - Bỏ dấu gạch đầu dòng Whisper (- )
    - Bỏ từ đệm đứng 1 mình hoặc đầu/cuối câu
    - Gom dấu lặp: ... → …, !! → !, ?? → ?
    - Xóa khoảng trắng thừa
    """
    if not text:
        return text

    result = text.strip()

    # Bỏ gạch đầu dòng kiểu Whisper
    result = re.sub(r'^-\s+', '', result)

    # Bỏ từ đệm đứng 1 mình (toàn bộ text chỉ là filler)
    if _FILLER_RE.fullmatch(result.strip()):
        return ""

    # Bỏ từ đệm ở đầu câu (theo sau bởi dấu phẩy hoặc khoảng trắng)
    # Không bỏ so/well nếu theo sau bởi dấu phẩy (kiểu liên kết câu: "So, the theorem...")
    result = re.sub(r'^(?:um|uh|uhm|hmm|hm|ah|eh|er|erm|like|okay|ok|right)[,\s]+',
                    '', result, flags=re.IGNORECASE)
    result = re.sub(r'^(?:so|well)\s+', '', result, flags=re.IGNORECASE)

    # Gom dấu lặp
    result = _MULTI_DOT_RE.sub('…', result)
    result = _MULTI_EXCL_RE.sub('!', result)
    result = _MULTI_QUES_RE.sub('?', result)

    # Xóa khoảng trắng thừa
    result = _MULTI_SPACE_RE.sub(' ', result).strip()

    return result


def polish_vietnamese(text):
    """
    Hậu xử lý bản dịch tiếng Việt:
    - Sửa khoảng trắng quanh dấu câu (X , Y → X, Y)
    - Đảm bảo 1 khoảng trắng sau dấu câu
    - Viết hoa chữ cái đầu câu
    - Xóa lặp dấu câu
    """
    if not text or not text.strip():
        return text

    result = text.strip()

    # Bỏ khoảng trắng trước dấu câu
    result = _SPACE_BEFORE_PUNCT_RE.sub(r'\1', result)

    # Thêm khoảng trắng sau dấu câu nếu thiếu
    result = _NO_SPACE_AFTER_PUNCT_RE.sub(r'\1 \2', result)

    # Gom dấu lặp
    result = _MULTI_DOT_RE.sub('…', result)
    result = _MULTI_EXCL_RE.sub('!', result)
    result = _MULTI_QUES_RE.sub('?', result)

    # Viết hoa chữ cái đầu câu
    if result:
        result = result[0].upper() + result[1:]
    result = re.sub(r'([.!?…]\s+)(\w)', lambda m: m.group(1) + m.group(2).upper(), result)

    # Xóa khoảng trắng thừa
    result = _MULTI_SPACE_RE.sub(' ', result).strip()

    return result


def _google_translate_api(text, source_lang, target_lang, client_type='dict-chrome-ex', timeout=10):
    """Gửi request trực tiếp đến Google Translate API endpoint."""
    url = 'https://translate.googleapis.com/translate_a/single'
    params = {'client': client_type, 'sl': source_lang, 'tl': target_lang, 'dt': 't'}
    res = _session.post(url, params=params, data={'q': text}, headers=_headers, timeout=timeout)
    if res.status_code == 200:
        data = res.json()
        if data and data[0]:
            translated = ''.join(part[0] for part in data[0] if part and part[0])
            if translated.strip():
                return translated
    return None


def translate_single_text(text, source_lang='auto', target_lang='vi'):
    """
    Dịch 1 văn bản với đa tầng fallback:
    1. Google API (client=dict-chrome-ex)
    2. Google API (client=gtx)
    3. deep_translator GoogleTranslator
    4. deep_translator MyMemoryTranslator
    """
    if not text or not text.strip():
        return text

    # Layer 1: dict-chrome-ex API
    try:
        res = _google_translate_api(text, source_lang, target_lang, client_type='dict-chrome-ex')
        if res: return res
    except Exception:
        pass

    # Layer 2: gtx API
    try:
        res = _google_translate_api(text, source_lang, target_lang, client_type='gtx')
        if res: return res
    except Exception:
        pass

    # Layer 3: deep_translator GoogleTranslator
    try:
        res = GoogleTranslator(source=source_lang, target=target_lang).translate(text)
        if res and res.strip(): return res
    except Exception:
        pass

    # Layer 4: MyMemoryTranslator
    try:
        src = 'en-US' if source_lang in ('en', 'auto') else source_lang
        tgt = 'vi-VN' if target_lang == 'vi' else target_lang
        res = MyMemoryTranslator(source=src, target=tgt).translate(text)
        if res and res.strip(): return res
    except Exception:
        pass

    return text


def _translate_batch_sentences(sentence_texts, source_lang, target_lang):
    """
    Dịch batch câu bằng marker [1], [2]... gom thành 1 request.
    Trả về list kết quả dịch, hoặc None nếu không tách được.
    """
    if not sentence_texts:
        return []

    lines = [f"[{i}] {t}" for i, t in enumerate(sentence_texts, 1)]
    combined = "\n".join(lines)

    translated_combined = translate_single_text(combined, source_lang, target_lang)
    if not translated_combined or translated_combined == combined:
        return None

    parts = _MARKER_RE.split(translated_combined)
    result_map = {}
    for j in range(1, len(parts) - 1, 2):
        try:
            idx = int(parts[j])
            result_map[idx] = parts[j + 1].strip()
        except (ValueError, IndexError):
            continue

    if len(result_map) == len(sentence_texts):
        return [result_map[i] for i in range(1, len(sentence_texts) + 1)]

    return None


def _translate_and_distribute(sentence_groups, segments, src_lang, target_lang, translation_cache):
    """
    Gom câu → dịch batch → rải bản dịch về từng segment.
    Trả về (count_ok, count_failed).
    """
    count_ok = 0
    count_failed = 0

    # Chuẩn bị text sạch cho mỗi cụm câu
    sentence_texts = []
    group_segment_indices = []  # Lưu (danh sách index trong segments) cho mỗi cụm

    for group in sentence_groups:
        cleaned_parts = []
        seg_indices = []
        for seg in group:
            idx = seg['_tmp_idx']  # index tạm gắn trước khi gọi hàm này
            cleaned = clean_source_text(seg['text'])
            if cleaned:
                cleaned_parts.append(cleaned)
                seg_indices.append(idx)

        if cleaned_parts and seg_indices:
            combined = " ".join(cleaned_parts)
            sentence_texts.append(combined)
            group_segment_indices.append(seg_indices)

    if not sentence_texts:
        return count_ok, count_failed

    # Dịch batch theo cụm câu (BATCH_SIZE câu mỗi lần)
    for batch_start in range(0, len(sentence_texts), BATCH_SIZE):
        batch_texts = sentence_texts[batch_start:batch_start + BATCH_SIZE]
        batch_groups = group_segment_indices[batch_start:batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (len(sentence_texts) + BATCH_SIZE - 1) // BATCH_SIZE

        print(f"[Batch {batch_num}/{total_batches}] Dịch {len(batch_texts)} câu ({src_lang} → {target_lang})...")

        # Thử batch translation
        batch_success = False
        for attempt in range(2):
            try:
                results = _translate_batch_sentences(batch_texts, src_lang, target_lang)
                if results is not None:
                    # Rải bản dịch về từng segment trong mỗi cụm câu
                    for translated_sentence, seg_indices in zip(results, batch_groups):
                        if len(seg_indices) == 1:
                            idx = seg_indices[0]
                            polished = polish_vietnamese(translated_sentence)
                            segments[idx]['translated_text'] = polished
                            segments[idx]['translation_ok'] = True
                            translation_cache[segments[idx]['text']] = polished
                            count_ok += 1
                        else:
                            source_texts = [segments[idx]['text'] for idx in seg_indices]
                            fractions = compute_source_fractions(source_texts)
                            parts = split_translation_back(translated_sentence, fractions)
                            for idx, part in zip(seg_indices, parts):
                                polished = polish_vietnamese(part)
                                segments[idx]['translated_text'] = polished
                                segments[idx]['translation_ok'] = True
                                translation_cache[segments[idx]['text']] = polished
                                count_ok += 1

                    batch_success = True
                    print(f"[Batch {batch_num}/{total_batches}] ✅ Thành công ({len(batch_texts)} câu)")
                    time.sleep(random.uniform(0.1, 0.3))
                    break
                else:
                    print(f"[Batch {batch_num}] Marker bị mất, thử lại...")
            except Exception as e:
                print(f"[Batch {batch_num}] Lỗi batch (lần {attempt+1}): {e}")
                time.sleep(1)

        if not batch_success:
            # Fallback: dịch từng câu riêng lẻ
            print(f"[Batch {batch_num}] Fallback dịch từng câu...")
            for sentence_text, seg_indices in zip(batch_texts, batch_groups):
                translated = translate_single_text(sentence_text, src_lang, target_lang)
                is_valid = (translated and translated.strip()
                            and (src_lang == 'vi' or translated.strip() != sentence_text.strip()))

                if not is_valid:
                    # Thử MyMemory
                    try:
                        alt = MyMemoryTranslator(
                            source='en-US' if src_lang == 'en' else src_lang,
                            target='vi-VN'
                        ).translate(sentence_text)
                        if alt and alt.strip() and alt.strip() != sentence_text.strip():
                            translated = alt
                            is_valid = True
                    except Exception:
                        pass

                if is_valid:
                    if len(seg_indices) == 1:
                        idx = seg_indices[0]
                        polished = polish_vietnamese(translated)
                        segments[idx]['translated_text'] = polished
                        segments[idx]['translation_ok'] = True
                        translation_cache[segments[idx]['text']] = polished
                        count_ok += 1
                    else:
                        source_texts = [segments[idx]['text'] for idx in seg_indices]
                        fractions = compute_source_fractions(source_texts)
                        parts = split_translation_back(translated, fractions)
                        for idx, part in zip(seg_indices, parts):
                            polished = polish_vietnamese(part)
                            segments[idx]['translated_text'] = polished
                            segments[idx]['translation_ok'] = True
                            translation_cache[segments[idx]['text']] = polished
                            count_ok += 1
                else:
                    for idx in seg_indices:
                        segments[idx]['translated_text'] = translated if translated else segments[idx]['text']
                        segments[idx]['translation_ok'] = False
                        count_failed += 1

                time.sleep(random.uniform(0.05, 0.15))

    return count_ok, count_failed


def translate_segments(segments, source_lang="auto", target_lang="vi"):
    """
    Dịch các đoạn văn bản sang ngôn ngữ đích.
    Gom segment vụn thành câu hoàn chỉnh trước khi dịch để tăng chất lượng.
    Có cache kết quả, kiểm tra ngôn ngữ, và tự động retry nếu gặp lỗi.
    Hỗ trợ checkpoint: segment có 'translation_ok'=True sẽ được reuse.
    """
    print(f"Đang dịch thuật các đoạn văn bản (target: {target_lang})...")

    translation_cache = {}

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

    # === Phase 2: Gom câu → dịch → rải bản dịch về segment ===
    by_lang = {}
    for idx, src_lang in needs_translation:
        by_lang.setdefault(src_lang, []).append(idx)

    for src_lang, indices in by_lang.items():
        # Lấy segments cần dịch, gắn index tạm để truy ngược
        lang_segments = []
        for idx in indices:
            segments[idx]['_tmp_idx'] = idx
            lang_segments.append(segments[idx])

        # Gom thành cụm câu
        sentence_groups = group_into_sentences(lang_segments, text_key='text')
        n_segments = len(lang_segments)
        n_sentences = len(sentence_groups)
        if n_sentences < n_segments:
            print(f"Gom câu ({src_lang}): {n_segments} segments → {n_sentences} câu "
                  f"(giảm ~{(1 - n_sentences/max(n_segments, 1))*100:.0f}%)")

        # Dịch và rải về
        ok, failed = _translate_and_distribute(
            sentence_groups, segments, src_lang, target_lang, translation_cache
        )
        count_ok += ok
        count_failed += failed

        # Dọn index tạm
        for idx in indices:
            segments[idx].pop('_tmp_idx', None)

    # Tổng kết
    total = len(segments)
    print(f"Dịch thuật hoàn tất! Đã dịch {count_ok}/{total}, reuse {count_reused}, lỗi {count_failed}")
    if count_failed > 0:
        print(f"⚠️ {count_failed} đoạn dịch lỗi sẽ được retry ở lần chạy kế tiếp.")
    return segments
