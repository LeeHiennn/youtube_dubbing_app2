"""
Module gom các segment vụn (Whisper) thành cụm câu hoàn chỉnh
và chia bản dịch ngược lại về từng segment theo tỷ lệ ký tự.
Dùng chung cho translator.py và cả 2 TTS engine.
"""

# Dấu kết thúc câu (Anh, Việt, Trung)
_SENTENCE_ENDS = set('.?!。？！')


def group_into_sentences(segments, max_gap=1.2, max_dur=8.0, max_chars=180, text_key='text'):
    """
    Gom segments liền nhau thành cụm câu hoàn chỉnh.

    Quy tắc ngắt cụm:
    - Segment trước kết thúc bằng dấu câu (.?!)
    - Khoảng cách thời gian giữa 2 segment > max_gap
    - Tổng thời lượng cụm > max_dur
    - Tổng ký tự cụm > max_chars

    Args:
        segments: danh sách segment dict (cần có 'start', 'end', và text_key)
        max_gap: khoảng trống tối đa (giây) giữa 2 segment trong cùng cụm
        max_dur: thời lượng tối đa (giây) của 1 cụm
        max_chars: số ký tự tối đa trong 1 cụm
        text_key: key lấy text ('text' cho translator, 'translated_text' cho TTS)

    Returns:
        list[list[segment]] — danh sách các cụm, mỗi cụm là list segment dict
    """
    valid = [s for s in segments if s.get(text_key, '').strip()]
    if not valid:
        return []

    groups = []
    current = [valid[0]]

    for seg in valid[1:]:
        prev = current[-1]
        gap = seg['start'] - prev['end']
        group_dur = seg['end'] - current[0]['start']
        curr_chars = sum(len(s.get(text_key, '')) for s in current)
        new_chars = curr_chars + len(seg.get(text_key, ''))

        prev_text = prev.get(text_key, '').strip()
        ends_sentence = prev_text and prev_text[-1] in _SENTENCE_ENDS

        if ends_sentence or gap > max_gap or group_dur > max_dur or new_chars > max_chars:
            groups.append(current)
            current = [seg]
        else:
            current.append(seg)

    if current:
        groups.append(current)

    return groups


def split_translation_back(translation, source_fractions):
    """
    Chia bản dịch cả câu về từng segment theo tỷ lệ độ dài ký tự nguồn.

    Cắt ĐÚNG biên từ (khoảng trắng) với tolerance ±10 ký tự.
    Segment cuối nhận toàn bộ phần còn lại.

    Args:
        translation: chuỗi bản dịch hoàn chỉnh
        source_fractions: list[float] — tỷ lệ ký tự nguồn mỗi segment (tổng ≈ 1.0)

    Returns:
        list[str] — bản dịch đã chia cho từng segment
    """
    n = len(source_fractions)
    if not translation or not source_fractions:
        return [translation or ""] * max(n, 1)

    if n == 1:
        return [translation.strip()]

    words = translation.split()
    if not words:
        return [""] * n

    total_len = sum(len(w) for w in words) + len(words) - 1  # tính cả khoảng trắng
    results = []
    used = 0
    TOLERANCE = 10

    for i in range(n):
        if i == n - 1:
            results.append(" ".join(words[used:]).strip())
            break

        target = int(total_len * source_fractions[i])
        char_count = 0
        best_j = used + 1  # ít nhất 1 từ

        for j in range(used, len(words)):
            wlen = len(words[j])
            if j > used:
                wlen += 1  # khoảng trắng
            char_count += wlen

            if char_count >= target - TOLERANCE:
                best_j = j + 1
                if char_count >= target + TOLERANCE:
                    break

        best_j = max(used + 1, min(best_j, len(words) - (n - i - 1)))
        results.append(" ".join(words[used:best_j]).strip())
        used = best_j

    # Đảm bảo trả về đúng số phần
    while len(results) < n:
        results.append("")

    return results


def compute_source_fractions(texts):
    """Tính tỷ lệ ký tự nguồn cho từng segment trong cụm."""
    lengths = [max(len(t.strip()), 1) for t in texts]
    total = sum(lengths)
    return [l / total for l in lengths]
