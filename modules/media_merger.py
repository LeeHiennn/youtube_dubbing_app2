import os
import subprocess

def merge_audio_with_video(video_path, audio_path, orig_volume=0.3, dub_volume=1.5,
                           bgm_audio_path=None, bgm_volume=0.6,
                           vocal_audio_path=None, orig_vocal_volume=0.0):
    """
    Ghép video với các luồng âm thanh đa kênh:
    - dub_volume: Âm lượng lồng tiếng AI (mặc định 1.5 để to rõ)
    - bgm_audio_path / bgm_volume: Nhạc nền tách từ Demucs (mặc định 0.6)
    - vocal_audio_path / orig_vocal_volume: Giọng gốc tách từ Demucs (mặc định 0.0 để tắt hẳn)
    - orig_volume: Âm lượng video gốc khi không tách giọng (mặc định 0.3)
    """
    print(f"Đang xử lý ghép âm thanh vào video (Dub={dub_volume}, BGM={bgm_volume}, Vocal={orig_vocal_volume}, Orig={orig_volume})...")
    
    base_dir = os.path.dirname(os.path.dirname(__file__))
    temp_dir = os.path.join(base_dir, 'temp')
    output_path = os.path.join(base_dir, 'final_dubbed_video.mp4')
    
    has_bgm = bgm_audio_path is not None and os.path.exists(bgm_audio_path)
    has_vocal = vocal_audio_path is not None and os.path.exists(vocal_audio_path) and orig_vocal_volume > 0

    # Cách 1: Sử dụng FFmpeg (Subprocess) -> Rất nhanh do không encode lại video
    try:
        if has_bgm:
            inputs = ['-i', video_path, '-i', audio_path, '-i', bgm_audio_path]
            filter_parts = [
                f'[1:a:0]volume={dub_volume}[dub]',
                f'[2:a:0]volume={bgm_volume}[bgm]'
            ]
            mix_inputs = ['[bgm]', '[dub]']
            
            if has_vocal:
                inputs.extend(['-i', vocal_audio_path])
                filter_parts.append(f'[3:a:0]volume={orig_vocal_volume}[voc]')
                mix_inputs.append('[voc]')
                
            num_inputs = len(mix_inputs)
            mix_str = "".join(mix_inputs)
            filter_complex = ";".join(filter_parts) + f";{mix_str}amix=inputs={num_inputs}:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.98[out]"
            
            command = [
                'ffmpeg', '-y',
                *inputs,
                '-filter_complex', filter_complex,
                '-c:v', 'copy',
                '-map', '0:v:0',
                '-map', '[out]',
                '-c:a', 'aac',
                '-b:a', '192k',
                output_path
            ]
        else:
            if orig_volume > 0:
                command = [
                    'ffmpeg', '-y',
                    '-i', video_path, '-i', audio_path,
                    '-filter_complex',
                    f'[0:a:0]volume={orig_volume}[orig];[1:a:0]volume={dub_volume}[dub];[orig][dub]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.98[out]',
                    '-c:v', 'copy',
                    '-map', '0:v:0',
                    '-map', '[out]',
                    '-c:a', 'aac',
                    '-b:a', '192k',
                    output_path
                ]
            else:
                command = [
                    'ffmpeg', '-y',
                    '-i', video_path, '-i', audio_path,
                    '-filter_complex',
                    f'[1:a:0]volume={dub_volume},alimiter=limit=0.98[out]',
                    '-c:v', 'copy',
                    '-map', '0:v:0',
                    '-map', '[out]',
                    '-c:a', 'aac',
                    '-b:a', '192k',
                    output_path
                ]
        
        result = subprocess.run(
            command, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            text=True
        )
        
        if result.returncode == 0:
            print(f"Ghép video thành công bằng FFmpeg siêu tốc!")
            return output_path
        else:
            print(f"FFmpeg ghép video trả về lỗi: {result.stderr}")
            print("Đang tự động chuyển sang dùng MoviePy (chậm hơn do cần re-encode)...")
    except FileNotFoundError:
        print("FFmpeg không có sẵn trong hệ thống (chưa cài đặt hoặc chưa thêm vào PATH).")
        print("Đang tự động chuyển sang dùng MoviePy (chậm hơn do cần re-encode)...")
        
    # Cách 2: Fallback MoviePy (Re-encode)
    try:
        from moviepy.editor import VideoFileClip, AudioFileClip, CompositeAudioClip
        video_clip = VideoFileClip(video_path)
        new_audio_clip = AudioFileClip(audio_path).volumex(dub_volume)
        
        audio_clips = [new_audio_clip]
        
        if has_bgm:
            bgm_clip = AudioFileClip(bgm_audio_path).volumex(bgm_volume)
            audio_clips.append(bgm_clip)
            if has_vocal:
                voc_clip = AudioFileClip(vocal_audio_path).volumex(orig_vocal_volume)
                audio_clips.append(voc_clip)
        elif orig_volume > 0 and video_clip.audio is not None:
            orig_audio = video_clip.audio.volumex(orig_volume)
            audio_clips.append(orig_audio)
            
        final_audio = CompositeAudioClip(audio_clips)
        if final_audio.duration > video_clip.duration:
            final_audio = final_audio.subclip(0, video_clip.duration)
            
        final_video = video_clip.set_audio(final_audio)
        
        # Tiến hành xuất video (render)
        final_video.write_videofile(
            output_path, 
            codec="libx264", 
            audio_codec="aac", 
            temp_audiofile=os.path.join(temp_dir, "temp-moviepy-audio.m4a"),
            remove_temp=True,
            logger=None 
        )
        
        video_clip.close()
        new_audio_clip.close()
        final_video.close()
        
        print(f"Ghép video hoàn tất bằng MoviePy!")
        return output_path
        
    except Exception as e:
        print(f"Lỗi khi ghép video: {e}")
        return None
