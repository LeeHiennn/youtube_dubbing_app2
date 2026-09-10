import os
import json
import whisper
from pydub import AudioSegment
from modules.audio_chunker import split_audio

_whisper_model = None
_current_model_size = None

def get_whisper_model(model_size):
    global _whisper_model, _current_model_size
    if _whisper_model is None or _current_model_size != model_size:
        print(f"Đang tải model Whisper '{model_size}'...")
        _whisper_model = whisper.load_model(model_size)
        _current_model_size = model_size
    return _whisper_model

import re
_HAS_LETTER = re.compile(r'[a-zA-Z\u00C0-\u024F\u1EA0-\u1EF9\u4E00-\u9FFF]')

def _filter_noise_segments(segments):
    MIN_DURATION = 0.3
    MIN_TEXT_LENGTH = 1
    
    filtered = []
    for seg in segments:
        text = seg.get('text', '').strip()
        duration = seg['end'] - seg['start']
        
        if duration < MIN_DURATION:
            continue
        if len(text) < MIN_TEXT_LENGTH:
            continue
        if not _HAS_LETTER.search(text):
            continue
            
        filtered.append(seg)
    
    print(f"Lọc nhiễu: {len(segments)} → {len(filtered)} segments")
    return filtered

def transcribe_audio(audio_path, model_size="small", language=None):
    """
    Nhận diện giọng nói từ file audio bằng OpenAI Whisper.
    Hỗ trợ chunking cho video dài > 10 phút.
    """
    temp_dir = os.path.dirname(audio_path)
    transcript_path = os.path.join(temp_dir, 'transcript.json')
    stt_checkpoint_path = os.path.join(temp_dir, 'stt_checkpoint.json')
    
    audio = AudioSegment.from_file(audio_path)
    duration_ms = len(audio)
    
    model = get_whisper_model(model_size)
    segments_data = []
    
    if duration_ms < 600000:
        print(f"Đang bóc băng file audio: {audio_path}...")
        result = model.transcribe(audio_path, language=language)
        for segment in result.get('segments', []):
            segments_data.append({
                'start': segment['start'],
                'end': segment['end'],
                'text': segment['text'].strip()
            })
    else:
        print("Video dài hơn 10 phút, bắt đầu chunking audio...")
        chunks = split_audio(audio_path, chunk_duration_ms=600000)
        
        processed_chunks = 0
        if os.path.exists(stt_checkpoint_path):
            try:
                with open(stt_checkpoint_path, 'r', encoding='utf-8') as f:
                    checkpoint_data = json.load(f)
                    segments_data = checkpoint_data.get('segments', [])
                    processed_chunks = checkpoint_data.get('processed_chunks', 0)
            except:
                pass
                
        for i, (chunk_path, start_offset_sec) in enumerate(chunks):
            if i < processed_chunks:
                print(f"Bỏ qua chunk {i} vì đã xử lý trước đó.")
                continue
                
            print(f"Đang bóc băng chunk {i}...")
            result = model.transcribe(chunk_path, language=language)
            
            for segment in result.get('segments', []):
                segments_data.append({
                    'start': segment['start'] + start_offset_sec,
                    'end': segment['end'] + start_offset_sec,
                    'text': segment['text'].strip()
                })
                
            processed_chunks += 1
            # Lưu checkpoint từng chunk
            with open(stt_checkpoint_path, 'w', encoding='utf-8') as f:
                json.dump({
                    'processed_chunks': processed_chunks,
                    'segments': segments_data
                }, f, ensure_ascii=False, indent=4)
                
    segments_data = _filter_noise_segments(segments_data)
    
    with open(transcript_path, 'w', encoding='utf-8') as f:
        json.dump(segments_data, f, ensure_ascii=False, indent=4)
        
    print(f"Bóc băng hoàn tất! Đã lưu transcript tại: {transcript_path}")
    return segments_data
