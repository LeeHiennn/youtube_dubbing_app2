import os
from pydub import AudioSegment

def split_audio(audio_path, chunk_duration_ms=600000):
    """
    Cắt audio thành chunk 10 phút.
    Lưu WAV vào temp/audio_chunks/.
    Trả list [(chunk_path, start_time_sec), ...]
    """
    base_dir = os.path.dirname(audio_path)
    chunks_dir = os.path.join(base_dir, 'audio_chunks')
    os.makedirs(chunks_dir, exist_ok=True)
    
    audio = AudioSegment.from_file(audio_path)
    base_name = os.path.splitext(os.path.basename(audio_path))[0]
    chunks = []
    
    for i, start in enumerate(range(0, len(audio), chunk_duration_ms)):
        chunk = audio[start:start + chunk_duration_ms]
        path = os.path.join(chunks_dir, f'{base_name}_chunk_{i}.wav')
        chunk.export(path, format='wav')
        chunks.append((path, start / 1000))
    
    return chunks
