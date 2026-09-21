import os
import sys
import io
import json
import shutil
import time

# Đảm bảo in được tiếng Việt trên console Windows (chỉ bật trên Windows, giữ line_buffering)
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)

from modules.downloader import download_media
from modules.voice_separator import separate_full
from modules.stt_engine import transcribe_audio
from modules.translator import translate_segments
from modules.tts_engine import generate_voiceover
from modules.media_merger import merge_audio_with_video 
from moviepy.editor import VideoFileClip
from modules.subtitles import build_srt, build_ass, burn_subtitles, check_support

def save_checkpoint(temp_dir, name, data):
    path = os.path.join(temp_dir, f'checkpoint_{name}.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_checkpoint(temp_dir, name):
    path = os.path.join(temp_dir, f'checkpoint_{name}.json')
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None

def _fmt_dur(seconds):
    """Format số giây thành chuỗi dễ đọc: 1h 23m 45s hoặc 2m 30s hoặc 15.2s."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m}m {s:02d}s"

def _print_timing_summary(timings, total_time):
    """In bảng tổng kết thời gian từng công đoạn."""
    print("\n" + "=" * 60)
    print("📊  BẢNG TỔNG KẾT THỜI GIAN LỒNG TIẾNG")
    print("=" * 60)
    print(f"{'Công đoạn':<35} {'Thời gian':>10} {'Tỷ lệ':>8}")
    print("-" * 60)
    for name, duration in timings:
        pct = (duration / total_time * 100) if total_time > 0 else 0
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        print(f"  {name:<33} {_fmt_dur(duration):>10} {pct:>5.1f}%  {bar}")
    print("-" * 60)
    print(f"  {'⏱️  TỔNG CỘNG':<33} {_fmt_dur(total_time):>10} {'100%':>6}")
    print("=" * 60 + "\n")

def cleanup_temp_files():
    """Dọn dẹp xóa mọi file rác trong thư mục temp/ sau khi hoàn thành thành công."""
    base_dir = os.path.dirname(__file__)
    temp_dir = os.path.join(base_dir, 'temp')
    
    if not os.path.exists(temp_dir):
        return
        
    try:
        shutil.rmtree(temp_dir)
        print("Đã dọn dẹp sạch sẽ thư mục lưu trữ tạm.")
    except Exception as e:
        print(f"Không thể xóa thư mục tạm: {e}")

def process_video(url, voice_choice, progress=None, model_size="small", orig_audio_volume=0.3, cancel_event=None, separate_vocals_flag=True, tts_engine="edge", voice_mode="auto", voice_instruct=None, ref_audio_path=None, source_lang="auto", burn_subs=True, sub_style="vi", dub_volume=1.5, bgm_volume=0.6, orig_vocal_volume=0.0):
    """
    Quy trình xử lý video, hỗ trợ Gradio Progress Bar, Checkpointing, Cancel Event, và Multi-channel Audio Mixing.
    """
    if progress: progress(0, desc="Bắt đầu quá trình...")
    
    # Thiết lập giọng đọc tùy theo lựa chọn trên giao diện (chỉ dùng cho edge-tts)
    if tts_engine == "edge":
        from modules.tts_engine import set_voice_name
        if voice_choice == "Nam (Nam Minh)":
            set_voice_name("vi-VN-NamMinhNeural")
        else:
            set_voice_name("vi-VN-HoaiMyNeural")

    base_dir = os.path.dirname(__file__)
    temp_dir = os.path.join(base_dir, 'temp')
    
    url_file = os.path.join(temp_dir, 'current_url.txt')
    if os.path.exists(url_file):
        with open(url_file, 'r', encoding='utf-8') as f:
            saved_url = f.read().strip()
        if saved_url != url:
            print("URL mới được phát hiện, tiến hành dọn dẹp data cũ...")
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass
                
    os.makedirs(temp_dir, exist_ok=True)
    with open(os.path.join(temp_dir, 'current_url.txt'), 'w', encoding='utf-8') as f:
        f.write(url)
    
    success = False
    pipeline_start = time.time()
    timings = []  # [(tên_bước, thời_gian_giây)]
    
    try:
        if cancel_event and cancel_event.is_set(): return
        
        # Bước 1: Tải Media
        if progress: progress(0.05, desc="Đang tải Video & Audio từ YouTube...")
        print(f"Bắt đầu xử lý URL: {url}")
        
        t0 = time.time()
        video_path = os.path.join(temp_dir, 'video.mp4')
        audio_path = os.path.join(temp_dir, 'video.mp3')
        if os.path.exists(video_path) and os.path.exists(audio_path):
            print("Phục hồi từ file media đã tải...")
        else:
            media_paths = download_media(url)
            video_path = media_paths['video']
            audio_path = media_paths['audio']
        timings.append(("📥 Tải video YouTube", time.time() - t0))
            
        if cancel_event and cancel_event.is_set(): return
        
        # Bước 1.5: Tách giọng nói (nếu bật)
        stt_audio_path = audio_path
        no_music_path = None
        vocal_path = audio_path
        if separate_vocals_flag:
            if progress: progress(0.15, desc="Đang tách giọng nói khỏi nhạc nền...")
            t0 = time.time()
            sep_result = separate_full(audio_path, temp_dir)
            stt_audio_path = sep_result["vocals"]
            vocal_path = sep_result["vocals"]
            no_music_path = sep_result["no_music"]
            timings.append(("🎵 Tách giọng (Demucs)", time.time() - t0))
            # Giải phóng VRAM sau Demucs để nhường chỗ cho Whisper
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass
        else:
            print("Bỏ qua bước tách giọng nói.")

        if cancel_event and cancel_event.is_set(): return
        
        # Bước 2: STT (Speech-to-Text)
        if progress: progress(0.3, desc=f"AI đang bóc băng lời nói (Whisper {model_size})...")
        
        t0 = time.time()
        stt_cache_key = f'stt_{source_lang}'
        segments = load_checkpoint(temp_dir, stt_cache_key)
        if not segments:
            # Fallback: tìm checkpoint cũ (key 'stt') để tương thích dữ liệu hiện có
            segments = load_checkpoint(temp_dir, 'stt')
        if segments:
            print(f"Phục hồi từ checkpoint STT ({source_lang})...")
        else:
            stt_lang = None if source_lang == "auto" else source_lang
            segments = transcribe_audio(stt_audio_path, model_size=model_size, language=stt_lang)
            save_checkpoint(temp_dir, stt_cache_key, segments)
        timings.append(("🎤 Bóc băng (Whisper STT)", time.time() - t0))
        
        # Giải phóng Whisper model khỏi VRAM để nhường chỗ cho TTS
        try:
            from modules.stt_engine import _whisper_model
            import torch
            if _whisper_model is not None:
                import modules.stt_engine as stt_mod
                del stt_mod._whisper_model
                stt_mod._whisper_model = None
                stt_mod._current_model_size = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except (ImportError, Exception):
            pass
            
        if cancel_event and cancel_event.is_set(): return
        
        # Kiểm tra Empty Speech
        if not segments:
            raise Exception("Video không chứa giọng nói hoặc không thể nhận diện được.")
            
        total_speech_duration = sum(seg['end'] - seg['start'] for seg in segments)
        if total_speech_duration < 1.0:
            raise Exception("Video không chứa giọng nói hoặc không thể nhận diện được.")
            
        # Bước 3: Dịch thuật
        if progress: progress(0.5, desc="Đang dịch ngữ nghĩa sang tiếng Việt...")
        
        t0 = time.time()
        trans_cache_key = f'translated_{source_lang}'
        trans_checkpoint = load_checkpoint(temp_dir, trans_cache_key)
        
        if trans_checkpoint:
            # Hỗ trợ cả định dạng mới (dict có "segments") và cũ (list trực tiếp)
            if isinstance(trans_checkpoint, dict) and 'segments' in trans_checkpoint:
                cached_segments = trans_checkpoint['segments']
            elif isinstance(trans_checkpoint, list):
                cached_segments = trans_checkpoint
            else:
                cached_segments = None
            
            if cached_segments:
                print(f"Phục hồi từ checkpoint dịch thuật ({source_lang}), chạy retry segment lỗi...")
                # Re-pass qua translate_segments để retry mọi segment translation_ok=False
                translated_segments = translate_segments(cached_segments, source_lang=source_lang)
                # Lưu đè checkpoint với định dạng mới (tự chữa segment lỗi)
                save_checkpoint(temp_dir, trans_cache_key, {
                    "source_lang": source_lang,
                    "target_lang": "vi",
                    "segments": translated_segments
                })
            else:
                translated_segments = translate_segments(segments, source_lang=source_lang)
                save_checkpoint(temp_dir, trans_cache_key, {
                    "source_lang": source_lang,
                    "target_lang": "vi",
                    "segments": translated_segments
                })
        else:
            translated_segments = translate_segments(segments, source_lang=source_lang)
            save_checkpoint(temp_dir, trans_cache_key, {
                "source_lang": source_lang,
                "target_lang": "vi",
                "segments": translated_segments
            })
            
        timings.append(("🌐 Dịch thuật (Google Translate)", time.time() - t0))
        if cancel_event and cancel_event.is_set(): return
        
        # Bước 4: TTS (Text-to-Speech)
        if progress: progress(0.7, desc=f"Đang sinh giọng đọc AI ({voice_choice})...")
        t0 = time.time()
        video_duration = None
        try:
            video_clip = VideoFileClip(video_path)
            video_duration = video_clip.duration
            video_clip.close()
        except:
            pass
        merged_audio_path = generate_voiceover(translated_segments, video_duration=video_duration, engine=tts_engine, voice_mode=voice_mode, voice_instruct=voice_instruct, ref_audio_path=ref_audio_path)
        tts_label = "🗣️ Sinh giọng (OmniVoice)" if tts_engine == "omnivoice" else "🗣️ Sinh giọng (Edge-TTS)"
        timings.append((tts_label, time.time() - t0))

        if not merged_audio_path or not os.path.exists(merged_audio_path):
            raise Exception("Không tạo được giọng đọc (có thể video gốc không có tiếng nói).")
            
        if cancel_event and cancel_event.is_set(): return
            
        # Bước 5: Ghép nối vào video
        if progress: progress(0.9, desc="Đang ghép đè âm thanh tiếng Việt vào Video gốc...")
        t0 = time.time()
        final_video = merge_audio_with_video(
            video_path,
            merged_audio_path,
            orig_volume=orig_audio_volume,
            dub_volume=dub_volume,
            bgm_audio_path=no_music_path if separate_vocals_flag else None,
            bgm_volume=bgm_volume,
            vocal_audio_path=vocal_path if separate_vocals_flag else None,
            orig_vocal_volume=orig_vocal_volume
        )
        timings.append(("🎬 Ghép âm thanh vào video", time.time() - t0))
        
        # Bước 6: Đốt phụ đề
        out1 = None
        vi_srt_path = None
        zh_srt_path = None
        
        if burn_subs:
            if progress: progress(0.95, desc="Đang đốt phụ đề vào video...")
            
            t0 = time.time()
            # Luôn build cả 2 file .srt
            vi_srt_path = os.path.join(temp_dir, 'sub_vi.srt')
            zh_srt_path = os.path.join(temp_dir, 'sub_zh.srt')
            build_srt(translated_segments, field='translated_text', out_path=vi_srt_path)
            build_srt(translated_segments, field='text', out_path=zh_srt_path)
            
            support = check_support()
            if not support['has_libass']:
                print("WARNING: Hệ thống không có ffmpeg hỗ trợ libass/subtitles. Bỏ qua bước đốt phụ đề.")
            else:
                out1 = final_video.replace('.mp4', '') + '_subtitled.mp4'
                try:
                    if sub_style == 'vi':
                        burn_subtitles(final_video, vi_srt_path, out1)
                    elif sub_style == 'zh':
                        burn_subtitles(final_video, zh_srt_path, out1)
                    elif sub_style == 'both':
                        both_ass_path = os.path.join(temp_dir, 'sub_both.ass')
                        build_ass(translated_segments, style='both', out_path=both_ass_path)
                        burn_subtitles(final_video, both_ass_path, out1)
                    
                    print(f"File phụ đề cuối (đã hard-burn): {out1}")
                except Exception as e:
                    print(f"Lỗi khi đốt phụ đề: {e}. Bỏ qua...")
                    out1 = None
            timings.append(("📝 Đốt phụ đề vào video", time.time() - t0))

        # In bảng tổng kết thời gian
        total_time = time.time() - pipeline_start
        _print_timing_summary(timings, total_time)
        
        if progress: progress(1.0, desc=f"Hoàn tất! Tổng thời gian: {_fmt_dur(total_time)}")
        print(f"Xử lý thành công! File xuất ra tại: {final_video}")
        if out1:
            print(f"File kèm phụ đề tại: {out1}")
        
        success = True
        return {
            "video": final_video,
            "subtitled_video": out1,
            "subtitle_vi": vi_srt_path,
            "subtitle_zh": zh_srt_path,
            "vocal": vocal_path,
            "no_music": no_music_path,
            "tts_audio": merged_audio_path,
            "segments": translated_segments
        }
        
    except Exception as e:
        print(f"Lỗi hệ thống: {e}")
        raise Exception(f"Đã xảy ra lỗi: {str(e)}")
    finally:
        if cancel_event and cancel_event.is_set():
            print("Pipeline bị hủy bởi người dùng")
        if success:
            pass # Bỏ cleanup_temp_files() tự động để GUI còn đọc được file audio
            # cleanup_temp_files()

def remix_video_audio(orig_volume=0.3, dub_volume=1.5, bgm_volume=0.6, orig_vocal_volume=0.0, burn_subs=True, sub_style="vi"):
    """
    Trộn lại âm thanh cho video trong thư mục temp/ với các mức âm lượng mới
    mà không cần bóc băng hay dịch lại.
    """
    base_dir = os.path.dirname(__file__)
    temp_dir = os.path.join(base_dir, 'temp')
    video_path = os.path.join(temp_dir, 'video.mp4')
    merged_audio_path = os.path.join(temp_dir, 'merged_audio.mp3')
    no_music_path = os.path.join(temp_dir, 'no_music.mp3')
    vocal_path = os.path.join(temp_dir, 'vocals.mp3')
    
    if not os.path.exists(video_path) or not os.path.exists(merged_audio_path):
        raise Exception("Chưa có video hoặc file lồng tiếng trong thư mục tạm. Vui lòng bấm 'Bắt đầu Lồng Tiếng' trước!")
        
    bgm_path = no_music_path if os.path.exists(no_music_path) else None
    voc_path = vocal_path if os.path.exists(vocal_path) else None
    
    final_video = merge_audio_with_video(
        video_path,
        merged_audio_path,
        orig_volume=orig_volume,
        dub_volume=dub_volume,
        bgm_audio_path=bgm_path,
        bgm_volume=bgm_volume,
        vocal_audio_path=voc_path,
        orig_vocal_volume=orig_vocal_volume
    )
    
    out1 = None
    vi_srt_path = os.path.join(temp_dir, 'sub_vi.srt')
    zh_srt_path = os.path.join(temp_dir, 'sub_zh.srt')
    
    if burn_subs:
        support = check_support()
        if support['has_libass']:
            out1 = final_video.replace('.mp4', '') + '_subtitled.mp4'
            try:
                if sub_style == 'vi' and os.path.exists(vi_srt_path):
                    burn_subtitles(final_video, vi_srt_path, out1)
                elif sub_style == 'zh' and os.path.exists(zh_srt_path):
                    burn_subtitles(final_video, zh_srt_path, out1)
                elif sub_style == 'both':
                    both_ass_path = os.path.join(temp_dir, 'sub_both.ass')
                    if os.path.exists(both_ass_path):
                        burn_subtitles(final_video, both_ass_path, out1)
            except Exception as e:
                print(f"Lỗi khi đốt phụ đề khi remix: {e}")
                out1 = None
                
    # Load segments from checkpoint if exists
    trans_cp = load_checkpoint(temp_dir, 'translated_auto') or load_checkpoint(temp_dir, 'translated_en') or load_checkpoint(temp_dir, 'translated_zh')
    segments = []
    if isinstance(trans_cp, dict) and 'segments' in trans_cp:
        segments = trans_cp['segments']
    elif isinstance(trans_cp, list):
        segments = trans_cp
        
    return {
        "video": final_video,
        "subtitled_video": out1,
        "subtitle_vi": vi_srt_path if os.path.exists(vi_srt_path) else None,
        "subtitle_zh": zh_srt_path if os.path.exists(zh_srt_path) else None,
        "vocal": voc_path,
        "no_music": bgm_path,
        "tts_audio": merged_audio_path,
        "segments": segments
    }


if __name__ == "__main__":
    # Test CLI (Dùng cho việc gõ lệnh trên terminal nếu không dùng GUI)
    print("=" * 60)
    print("===  PIPELINE LỒNG TIẾNG YOUTUBE TỰ ĐỘNG (EN -> VI)  ===")
    print("=" * 60)
    test_url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    process_video(test_url, "Nữ (Hoài My)", model_size="small")
