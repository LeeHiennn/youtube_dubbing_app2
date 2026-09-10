import os
import subprocess
import json
import shutil
import tempfile

SEPARATED_VOCAL_NAME = "vocals.mp3"

def _get_device():
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except ImportError:
        pass
    return "cpu"

def is_demucs_available():
    try:
        import demucs
        return True
    except ImportError:
        return False

def separate_full(audio_path, temp_dir):
    """
    Tách giọng nói khỏi nhạc nền bằng Demucs htdemucs.
    Trả về dict chứa path file vocal và no_music.
    Nếu không có Demucs hoặc lỗi -> fallback về audio gốc cho vocal, rỗng cho no_music.
    """
    checkpoint_file = os.path.join(temp_dir, 'separated_checkpoint.json')
    vocal_output = os.path.join(temp_dir, "vocals.mp3")
    no_music_output = os.path.join(temp_dir, "no_music.mp3")

    # Fallback mặc định (khi lỗi hoặc tắt demucs)
    result = {
        "vocals": audio_path,
        "no_music": None
    }

    if os.path.exists(vocal_output) and os.path.exists(no_music_output):
        print("Phục hồi từ vocal và nhạc nền đã tách trước đó...")
        result["vocals"] = vocal_output
        result["no_music"] = no_music_output
        return result

    if not is_demucs_available():
        print("Demucs chưa được cài đặt, dùng audio gốc cho STT.")
        return result

    device = _get_device()
    if device == "cpu":
        print("Cảnh báo: không có GPU, tách vocal trên CPU sẽ rất chậm!")

    print(f"Đang tách giọng nói bằng Demucs (device={device})...")

    demucs_out = os.path.join(temp_dir, 'demucs_output')
    os.makedirs(demucs_out, exist_ok=True)

    try:
        subprocess.run(
            [
                'python', '-m', 'demucs',
                '--two-stems', 'vocals',
                '-o', demucs_out,
                '--device', device,
                '--mp3', '--mp3-bitrate', '192',
                audio_path,
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        print("Demucs timeout, dùng audio gốc.")
        return result
    except subprocess.CalledProcessError as e:
        print(f"Demucs lỗi: {e.stderr.decode()[:200]}, dùng audio gốc.")
        return result

    base_name = os.path.splitext(os.path.basename(audio_path))[0]
    stem_dir = os.path.join(demucs_out, 'htdemucs', base_name)
    vocal_file = os.path.join(stem_dir, 'vocals.mp3')
    no_vocals_file = os.path.join(stem_dir, 'no_vocals.mp3')

    if os.path.exists(vocal_file) and os.path.exists(no_vocals_file):
        shutil.copy2(vocal_file, vocal_output)
        shutil.copy2(no_vocals_file, no_music_output)
        shutil.rmtree(demucs_out, ignore_errors=True)

        with open(checkpoint_file, 'w') as f:
            json.dump({'separated': True}, f)

        print(f"Tách giọng nói hoàn tất.")
        result["vocals"] = vocal_output
        result["no_music"] = no_music_output
        return result
    else:
        print("Demucs không tạo được đủ file, dùng audio gốc.")
        return result
