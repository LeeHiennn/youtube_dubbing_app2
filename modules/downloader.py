import os
import sys
import subprocess
import yt_dlp

def download_media(url, quality="1080"):
    """
    Tải video chất lượng cao (1080p, 2K/4K, hoặc 720p) và trích xuất âm thanh (mp3) từ YouTube.
    Lưu vào thư mục temp/ và trả về đường dẫn 2 file.
    """
    # Lấy đường dẫn thư mục gốc của project (cha của thư mục modules/)
    base_dir = os.path.dirname(os.path.dirname(__file__))
    temp_dir = os.path.join(base_dir, 'temp')
    os.makedirs(temp_dir, exist_ok=True)
    
    video_path = os.path.join(temp_dir, 'video.mp4')
    audio_path = os.path.join(temp_dir, 'video.mp3')
    
    # Xóa file cũ nếu đã tồn tại để tránh yt-dlp tạo tên file mới (vd: video(1).mp4)
    if os.path.exists(video_path):
        os.remove(video_path)
    if os.path.exists(audio_path):
        os.remove(audio_path)

    # Cấu hình chất lượng video
    q_str = str(quality).lower()
    if "max" in q_str or "4k" in q_str or "2k" in q_str or "cao nhất" in q_str:
        format_selector = 'bestvideo+bestaudio/best'
        target_quality_name = "Tối đa (2K/4K nếu có)"
    elif "720" in q_str:
        format_selector = 'bestvideo[height<=720]+bestaudio/best[height<=720]/best'
        target_quality_name = "HD 720p"
    else:
        format_selector = 'bestvideo[height<=1080]+bestaudio/best[height<=1080]/best'
        target_quality_name = "Full HD 1080p"

    print(f"🎬 Bắt đầu tải video từ YouTube (Chất lượng mục tiêu: {target_quality_name})...")
        
    base_ydl_opts = {
        'format': format_selector,
        'outtmpl': os.path.join(temp_dir, 'video.%(ext)s'),
        'merge_output_format': 'mp4',
        'keepvideo': True, # Giữ lại file video gốc sau khi đã trích xuất audio
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'noplaylist': True,
        'js_runtimes': {
            'node': {},
        },
    }
    
    cookie_file = os.path.join(base_dir, 'cookies.txt')
    downloaded = False
    
    # 1. Thử dùng cookie file (nếu người dùng có upload cookies.txt)
    if os.path.exists(cookie_file):
        try:
            print("Đang thử tải video bằng cookies.txt...")
            ydl_opts_cookie = dict(base_ydl_opts)
            ydl_opts_cookie['cookiefile'] = cookie_file
            with yt_dlp.YoutubeDL(ydl_opts_cookie) as ydl:
                ydl.download([url])
            downloaded = True
        except Exception as e:
            print(f"Cảnh báo: Dùng cookies.txt gặp lỗi ({e}). Đang chuyển sang phương thức khác...")
            downloaded = False

    # 2. Nếu trên Windows và chưa tải được: thử cookie từ Chrome
    if not downloaded and sys.platform == 'win32':
        try:
            ydl_opts_chrome = dict(base_ydl_opts)
            ydl_opts_chrome['cookiesfrombrowser'] = ('chrome',)
            with yt_dlp.YoutubeDL(ydl_opts_chrome) as ydl:
                ydl.download([url])
            downloaded = True
        except Exception:
            downloaded = False

    # 3. Tải bằng client mặc định của yt-dlp (tự thương lượng visionos/web, tránh dính lỗi SABR 403 của ios/android)
    if not downloaded:
        try:
            ydl_opts_default = dict(base_ydl_opts)
            with yt_dlp.YoutubeDL(ydl_opts_default) as ydl:
                ydl.download([url])
            downloaded = True
        except Exception as e:
            print(f"Client mặc định thất bại: {e}. Đang thử client dự phòng...")
            downloaded = False

    # 4. Fallback: loại trừ android_sdkless
    if not downloaded:
        try:
            ydl_opts_fallback = dict(base_ydl_opts)
            ydl_opts_fallback['extractor_args'] = {
                'youtube': {
                    'player_client': ['default', '-android_sdkless']
                }
            }
            with yt_dlp.YoutubeDL(ydl_opts_fallback) as ydl:
                ydl.download([url])
            downloaded = True
        except Exception as e:
            print(f"Fallback 1 thất bại: {e}. Đang thử client di động...")
            downloaded = False

    # 5. Fallback cuối cùng: Mobile client
    if not downloaded:
        ydl_opts_mobile = dict(base_ydl_opts)
        ydl_opts_mobile['extractor_args'] = {
            'youtube': {
                'player_client': ['ios', 'android']
            }
        }
        with yt_dlp.YoutubeDL(ydl_opts_mobile) as ydl:
            ydl.download([url])
    # Kiểm tra và thông báo độ phân giải video thực tế đã tải
    if os.path.exists(video_path):
        try:
            cmd = ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                   '-show_entries', 'stream=width,height', '-of', 'csv=s=x:p=0', video_path]
            probe_res = subprocess.run(cmd, capture_output=True, text=True)
            if probe_res.returncode == 0 and 'x' in probe_res.stdout:
                parts = probe_res.stdout.strip().split('x')
                w, h = int(parts[0]), int(parts[1])
                print(f"✅ Video tải về thành công với độ phân giải cao: {w}x{h} ({h}p)!")
        except Exception:
            pass

    return {
        'video': video_path,
        'audio': audio_path
    }
