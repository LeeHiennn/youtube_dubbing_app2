import os
import yt_dlp

def download_media(url):
    """
    Tải video (max 1080p, mp4) và trích xuất âm thanh (mp3) từ YouTube.
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
        
    ydl_opts = {
        'format': 'bestvideo[height<=1080]+bestaudio/best[height<=1080]/best',
        'outtmpl': os.path.join(temp_dir, 'video.%(ext)s'),
        'merge_output_format': 'mp4',
        'keepvideo': True, # Giữ lại file video gốc sau khi đã trích xuất audio
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'noplaylist': True,
    }
    
    cookie_file = os.path.join(base_dir, 'cookies.txt')
    if os.path.exists(cookie_file):
        ydl_opts['cookiefile'] = cookie_file
    else:
        ydl_opts['cookiesfrombrowser'] = ('chrome',)
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
        
    return {
        'video': video_path,
        'audio': audio_path
    }
