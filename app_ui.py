import os
import shutil
import gradio as gr
import threading
import subprocess
import json
from main import process_video, remix_video_audio

import queue
import sys

SAVED_VOICES_DIR = os.path.join(os.path.dirname(__file__), 'saved_voices')
os.makedirs(SAVED_VOICES_DIR, exist_ok=True)

def list_saved_voices():
    """Trả về danh sách tên giọng đã lưu."""
    if not os.path.exists(SAVED_VOICES_DIR):
        return []
    return sorted([
        d for d in os.listdir(SAVED_VOICES_DIR)
        if os.path.isdir(os.path.join(SAVED_VOICES_DIR, d))
        and os.path.exists(os.path.join(SAVED_VOICES_DIR, d, 'ref_audio.wav'))
    ])

def get_saved_voice_path(name):
    return os.path.join(SAVED_VOICES_DIR, name, 'ref_audio.wav')

cancel_event = threading.Event()

class OutputCapture:
    def __init__(self, original_stdout, q):
        self.original_stdout = original_stdout
        self.q = q
    def write(self, text):
        self.original_stdout.write(text)
        if text.strip():
            self.q.put(text)
    def flush(self):
        self.original_stdout.flush()

def detect_duration(url):
    if not url or ("youtube.com" not in url and "youtu.be" not in url):
        return "⏱ Duration: —"
        
    try:
        import os
        cookie_file = os.path.join(os.path.dirname(__file__), 'cookies.txt')
        if os.path.exists(cookie_file):
            cmd = ['yt-dlp', '--cookies', cookie_file, '--print', 'duration', url]
        else:
            cmd = ['yt-dlp', '--cookies-from-browser', 'chrome', '--print', 'duration', url]
            
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=10
        )
        duration = int(result.stdout.strip())
        mins, secs = divmod(duration, 60)
        if duration > 1800:
            warning = "⚠️ Video dài >30 phút — sẽ chunk và dùng Whisper tiny"
        elif duration > 600:
            warning = "⚠️ Video dài >10 phút — sẽ chunk tự động"
        else:
            warning = "✅ OK"
        return f"⏱ Duration: {mins}:{secs:02d} ({warning})"
    except:
        return "⏱ Duration: không xác định"

def preview_voice(text, tts_engine_choice, voice_mode_choice, voice_instruct, ref_audio, saved_voice):
    """Tạo file audio preview từ text mẫu để user nghe thử giọng trước khi lồng tiếng."""
    if not text or not text.strip():
        raise gr.Error("Vui lòng nhập câu văn mẫu để preview!")

    engine_map = {
        "edge-tts (Online - Mặc định)": "edge",
        "OmniVoice (Local GPU)": "omnivoice",
    }
    engine = engine_map.get(tts_engine_choice, "edge")

    out_dir = os.path.join(os.path.dirname(__file__), 'temp')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, 'preview_output.wav')

    if engine == "edge":
        import asyncio
        from modules.tts_engine import _generate_edge_tts_async, set_voice_name
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_generate_edge_tts_async(text, out_path))
        finally:
            loop.close()
        return out_path

    # OmniVoice
    from modules.tts_engine_omnivoice import _get_model
    model = _get_model()

    if voice_mode_choice == "🎭 Clone giọng (Voice Clone)":
        audio_path = None
        if saved_voice and saved_voice != "-- Chọn giọng đã lưu --":
            audio_path = get_saved_voice_path(saved_voice)
        elif ref_audio is not None:
            audio_path = ref_audio

        if audio_path and os.path.exists(audio_path):
            from modules.stt_engine import transcribe_audio
            ref_segments = transcribe_audio(audio_path, model_size="tiny")
            ref_text = " ".join(seg['text'] for seg in ref_segments) if ref_segments else None
            audio_list = model.generate(text=text, ref_audio=audio_path, ref_text=ref_text)
        else:
            audio_list = model.generate(text=text, instruct="female")
    elif voice_mode_choice == "🎨 Thiết kế giọng (Voice Design)" and voice_instruct:
        audio_list = model.generate(text=text, instruct=voice_instruct)
    else:
        audio_list = model.generate(text=text, instruct="female")

    import soundfile as sf
    sf.write(out_path, audio_list[0], 24000)
    return out_path


def run_dubbing(url, source_lang_ui, voice_name, whisper_model, orig_volume, dub_volume, bgm_volume, orig_vocal_volume, separate_vocals_flag, burn_subs_flag, sub_style_ui, tts_engine_choice, voice_mode_choice, voice_instruct, ref_audio, saved_voice, progress=gr.Progress()):
    global cancel_event
    cancel_event.clear()
    
    from modules.subtitles import check_support
    support = check_support()
    print(f"libass available: {support['has_libass']}")
    
    if not url or ("youtube.com" not in url and "youtu.be" not in url):
        raise gr.Error("Link YouTube không hợp lệ!")
    
    engine_map = {
        "edge-tts (Online - Mặc định)": "edge",
        "OmniVoice (Local GPU)": "omnivoice",
    }
    engine = engine_map.get(tts_engine_choice, "edge")
    
    voice_mode = "auto"
    instruct = None
    if engine == "omnivoice":
        if voice_mode_choice == "🤖 Auto Voice (Tự động)":
            voice_mode = "auto"
            instruct = "female"
        elif voice_mode_choice == "🎨 Thiết kế giọng (Voice Design)":
            voice_mode = "design"
            instruct = voice_instruct if voice_instruct else "female"
        elif voice_mode_choice == "🎭 Clone giọng (Voice Clone)":
            voice_mode = "clone"
    
    ref_audio_path = None
    if voice_mode == "clone":
        if saved_voice and saved_voice != "-- Chọn giọng đã lưu --":
            ref_audio_path = get_saved_voice_path(saved_voice)
        elif ref_audio is not None:
            ref_audio_path = os.path.join(os.path.dirname(__file__), 'temp', 'ref_audio.wav')
            os.makedirs(os.path.dirname(ref_audio_path), exist_ok=True)
            shutil.copy(ref_audio, ref_audio_path)
    
    q = queue.Queue()
    original_stdout = sys.stdout
    capture = OutputCapture(original_stdout, q)
    sys.stdout = capture
    
    lang_map = {"Tự động (Auto)": "auto", "English": "en", "中文 (Tiếng Trung)": "zh"}
    source_lang = lang_map.get(source_lang_ui, "auto")
    style_map = {"Tiếng Việt (lồng tiếng)": "vi", "Cả hai (Việt + Trung)": "both", "Tiếng Trung gốc": "zh"}
    sub_style = style_map.get(sub_style_ui, "vi")
    
    log_history = ""
    yield log_history, None, None, None, None, None, None, None, None
    
    def worker():
        try:
            result_dict = process_video(
                url, voice_name,
                model_size=whisper_model,
                orig_audio_volume=orig_volume,
                dub_volume=dub_volume,
                bgm_volume=bgm_volume,
                orig_vocal_volume=orig_vocal_volume,
                cancel_event=cancel_event,
                progress=progress,
                separate_vocals_flag=separate_vocals_flag,
                tts_engine=engine,
                voice_mode=voice_mode,
                voice_instruct=instruct,
                ref_audio_path=ref_audio_path if voice_mode == "clone" else None,
                source_lang=source_lang,
                burn_subs=burn_subs_flag,
                sub_style=sub_style,
            )
            q.put(("DONE", result_dict))
        except Exception as e:
            q.put(("ERROR", str(e)))
            
    t = threading.Thread(target=worker)
    t.start()
    
    try:
        while True:
            try:
                msg = q.get(timeout=0.2)
                if isinstance(msg, tuple):
                    status, result = msg
                    if status == "ERROR":
                        raise gr.Error(result)
                    else:
                        # process dataframe
                        segments = result.get("segments", [])
                        df_data = []
                        fail_count = 0
                        for seg in segments:
                            start_str = f"{int(seg['start']//60)}:{int(seg['start']%60):02d}"
                            end_str = f"{int(seg['end']//60)}:{int(seg['end']%60):02d}"
                            translated = seg.get('translated_text', '')
                            if seg.get('translation_ok') is False:
                                translated = "[⚠ LỖI DỊCH] " + translated
                                fail_count += 1
                            df_data.append([f"{start_str} - {end_str}", seg.get('text', ''), translated])
                        
                        if fail_count > 0:
                            log_history += f"\n⚠ {fail_count} đoạn dịch lỗi sẽ retry lần sau.\n"

                        yield log_history, result.get("video"), result.get("subtitled_video"), result.get("subtitle_vi"), result.get("subtitle_zh"), result.get("no_music"), result.get("vocal"), result.get("tts_audio"), df_data
                        break
                else:
                    log_history += msg + "\n"
                    lines = log_history.strip().split("\n")
                    if len(lines) > 20:
                        log_history = "\n".join(lines[-20:]) + "\n"
                    yield log_history, None, None, None, None, None, None, None, None
            except queue.Empty:
                if not t.is_alive():
                    break
                yield log_history, None, None, None, None, None, None, None, None
    finally:
        sys.stdout = original_stdout

def run_remix(orig_volume, dub_volume, bgm_volume, orig_vocal_volume, burn_subs_flag, sub_style_ui):
    style_map = {"Tiếng Việt (lồng tiếng)": "vi", "Cả hai (Việt + Trung)": "both", "Tiếng Trung gốc": "zh"}
    sub_style = style_map.get(sub_style_ui, "vi")
    
    try:
        res = remix_video_audio(
            orig_volume=orig_volume,
            dub_volume=dub_volume,
            bgm_volume=bgm_volume,
            orig_vocal_volume=orig_vocal_volume,
            burn_subs=burn_subs_flag,
            sub_style=sub_style
        )
        
        segments = res.get("segments", [])
        df_data = []
        fail_count = 0
        for seg in segments:
            start_str = f"{int(seg['start']//60)}:{int(seg['start']%60):02d}"
            end_str = f"{int(seg['end']//60)}:{int(seg['end']%60):02d}"
            translated = seg.get('translated_text', '')
            if seg.get('translation_ok') is False:
                translated = "[⚠ LỖI DỊCH] " + translated
                fail_count += 1
            df_data.append([f"{start_str} - {end_str}", seg.get('text', ''), translated])
            
        log_msg = f"🎛️ Trộn lại âm thanh thành công! [Lồng tiếng={dub_volume}, Nhạc nền={bgm_volume}, Giọng gốc={orig_vocal_volume}, Video gốc={orig_volume}]\n"
        if fail_count > 0:
            log_msg += f"⚠ {fail_count} đoạn dịch lỗi sẽ retry lần sau.\n"
            
        return log_msg, res.get("video"), res.get("subtitled_video"), res.get("subtitle_vi"), res.get("subtitle_zh"), res.get("no_music"), res.get("vocal"), res.get("tts_audio"), df_data
    except Exception as e:
        raise gr.Error(f"Lỗi khi trộn lại âm thanh: {str(e)}")

def cancel_processing():
    global cancel_event
    cancel_event.set()

with gr.Blocks(title="AI YouTube Dubber", theme=gr.themes.Soft()) as app:
    gr.Markdown("# 🎙️ Trợ lý Lồng Tiếng YouTube AI")
    gr.Markdown("Lồng tiếng Anh → Việt, giữ tiếng gốc, xử lý video dài, chuẩn hóa phát âm.")
    
    with gr.Row():
        url_input = gr.Textbox(
            label="🔗 Link YouTube", scale=3,
            placeholder="https://www.youtube.com/watch?v=..."
        )
        source_lang_dropdown = gr.Dropdown(
            choices=["Tự động (Auto)", "English", "中文 (Tiếng Trung)"],
            value="Tự động (Auto)", label="🌐 Ngôn ngữ nguồn", scale=1
        )
        duration_display = gr.Markdown("⏱ Duration: —", scale=1)
    
    with gr.Row():
        tts_engine_dropdown = gr.Dropdown(
            choices=["edge-tts (Online - Mặc định)", "OmniVoice (Local GPU)"],
            value="edge-tts (Online - Mặc định)", label="Engine TTS", scale=1
        )
        edge_voice_dropdown = gr.Dropdown(
            choices=["Nữ (Hoài My)", "Nam (Nam Minh)"],
            value="Nữ (Hoài My)", label="Giọng đọc edge-tts", scale=1
        )
        whisper_dropdown = gr.Dropdown(
            choices=["tiny", "base", "small", "medium"],
            value="small", label="Model Whisper", scale=1
        )
    with gr.Accordion("🎚️ Bộ điều chỉnh âm lượng (Volume Mixer)", open=True):
        with gr.Row():
            dub_volume_slider = gr.Slider(
                minimum=0.0, maximum=3.0, value=1.5, step=0.1,
                label="🔊 Âm lượng lồng tiếng AI (TTS)",
                info="Mặc định 1.5 (to rõ, nổi bật). Tăng lên 1.8 - 2.5 nếu muốn to hơn nữa",
                scale=1
            )
            bgm_volume_slider = gr.Slider(
                minimum=0.0, maximum=2.0, value=0.6, step=0.05,
                label="🎵 Âm lượng nhạc nền (BGM)",
                info="Mặc định 0.6 (nhạc nền sạch sau khi tách lời nói)",
                scale=1
            )
            orig_vocal_volume_slider = gr.Slider(
                minimum=0.0, maximum=1.0, value=0.0, step=0.05,
                label="🗣️ Âm lượng giọng gốc (Original Vocal)",
                info="Mặc định 0.0 (tắt sạch tiếng người gốc)",
                scale=1
            )
            orig_volume_slider = gr.Slider(
                minimum=0.0, maximum=1.0, value=0.3, step=0.05,
                label="📻 Âm lượng video gốc (khi không tách)",
                info="Chỉ dùng khi bỏ chọn 'Tách giọng nói'",
                scale=1
            )
    
    with gr.Row():
        separate_vocals_checkbox = gr.Checkbox(
            label="Tách giọng nói (Demucs)", value=True,
            scale=1
        )
        burn_subs_checkbox = gr.Checkbox(
            label="🔥 Chèn phụ đề vào video", value=True,
            scale=1
        )
        sub_style_dropdown = gr.Dropdown(
            choices=["Tiếng Việt (lồng tiếng)", "Cả hai (Việt + Trung)", "Tiếng Trung gốc"],
            value="Tiếng Việt (lồng tiếng)", label="Phụ đề hiển thị", scale=2
        )
    
    with gr.Row():
        voice_mode_radio = gr.Radio(
            choices=["🤖 Auto Voice (Tự động)", "🎨 Thiết kế giọng (Voice Design)", "🎭 Clone giọng (Voice Clone)"],
            value="🤖 Auto Voice (Tự động)", label="Chế độ giọng đọc OmniVoice",
            visible=False
        )
        voice_instruct_text = gr.Textbox(
            label="Mô tả giọng (female, male, british accent, low pitch, ...)",
            placeholder="female, british accent, low pitch",
            value="female",
            visible=False, scale=2
        )
    
    with gr.Row(visible=False) as clone_row:
        saved_voice_dropdown = gr.Dropdown(
            choices=["-- Chọn giọng đã lưu --"],
            value="-- Chọn giọng đã lưu --",
            label="🎤 Giọng đã lưu",
            scale=2
        )
        ref_audio_upload = gr.Audio(
            label="📤 Hoặc upload giọng mới (3-10s)",
            type="filepath",
            scale=2
        )
    
    with gr.Row(visible=False) as save_row:
        new_voice_name = gr.Textbox(
            label="Tên giọng muốn lưu",
            placeholder="VD: Giong Nam A, Giong Nu B, ...",
            scale=2
        )
        save_voice_btn = gr.Button("💾 Lưu giọng này", variant="secondary", scale=1)
    
    gr.Markdown("---")
    gr.Markdown("### 🎧 Nghe thử giọng đọc")
    with gr.Row():
        preview_text = gr.Textbox(
            label="Nhập câu mẫu để preview",
            placeholder="Xin chào, đây là giọng đọc tiếng Việt từ AI.",
            value="Xin chào, đây là giọng đọc tiếng Việt từ AI.",
            scale=3
        )
        preview_btn = gr.Button("🔊 Nghe thử", variant="secondary", scale=1)
    preview_audio = gr.Audio(label="Kết quả preview", type="filepath")
    gr.Markdown("---")
    
    with gr.Row():
        submit_btn = gr.Button("🚀 Bắt đầu Lồng Tiếng", variant="primary", scale=2)
        remix_btn = gr.Button("🎛️ Trộn lại âm lượng (Re-mix)", variant="secondary", scale=1)
        cancel_btn = gr.Button("⏹ Hủy", variant="stop", scale=1)
    
    log_output = gr.Textbox(
        label="📋 Log", lines=8, max_lines=20,
        placeholder="Log (xem console để biết chi tiết)...",
        interactive=False
    )
    
    gr.Markdown("## 🎬 Video & Âm thanh chi tiết")
    with gr.Row():
        with gr.Column(scale=2):
            video_output = gr.Video(label="🎬 Thành quả (Video + Audio trộn)")
            subtitled_video_output = gr.Video(label="🎬 Video + Phụ đề (hard-burn)")
            with gr.Row():
                subtitle_vi_output = gr.File(label="📄 Phụ đề Việt (.srt)")
                subtitle_zh_output = gr.File(label="📄 Phụ đề gốc (.srt)")
        with gr.Column(scale=1):
            no_music_output = gr.Audio(label="🌿 Âm thanh nền (Không có tiếng nói)")
            vocal_output = gr.Audio(label="🗣 Giọng nói gốc (Vocal EN)")
            tts_output = gr.Audio(label="🔊 Lồng tiếng Việt (TTS VI)")

    gr.Markdown("## 📝 Transcript đã dịch (Bảng kiểm tra)")
    transcript_output = gr.Dataframe(
        headers=["Thời gian", "Ngôn ngữ gốc", "Tiếng Việt (Đã dịch)"],
        wrap=True,
        interactive=False
    )
    
    def refresh_saved_voices():
        voices = list_saved_voices()
        choices = ["-- Chọn giọng đã lưu --"] + voices if voices else ["-- Chọn giọng đã lưu --"]
        return gr.update(choices=choices, value="-- Chọn giọng đã lưu --")

    def update_omnivoice_controls(engine):
        show_omni = engine == "OmniVoice (Local GPU)"
        show_edge = engine == "edge-tts (Online - Mặc định)"
        return {
            voice_mode_radio: gr.update(visible=show_omni),
            voice_instruct_text: gr.update(visible=False),
            clone_row: gr.update(visible=False),
            save_row: gr.update(visible=False),
            edge_voice_dropdown: gr.update(visible=show_edge),
        }
    
    def update_voice_mode_controls(mode):
        show_instruct = mode == "🎨 Thiết kế giọng (Voice Design)"
        show_clone = mode == "🎭 Clone giọng (Voice Clone)"
        return {
            voice_instruct_text: gr.update(visible=show_instruct),
            clone_row: gr.update(visible=show_clone),
            save_row: gr.update(visible=show_clone),
        }
    
    def save_voice(audio_path, name):
        if not name or not name.strip():
            raise gr.Error("Vui lòng nhập tên cho giọng muốn lưu!")
        if not audio_path:
            raise gr.Error("Vui lòng upload file audio trước khi lưu!")
        name = name.strip()
        voice_dir = os.path.join(SAVED_VOICES_DIR, name)
        os.makedirs(voice_dir, exist_ok=True)
        dst = os.path.join(voice_dir, 'ref_audio.wav')
        shutil.copy(audio_path, dst)
        print(f"Đã lưu giọng '{name}' tại {dst}")
        choices = ["-- Chọn giọng đã lưu --"] + list_saved_voices()
        return (
            gr.update(choices=choices, value=name),
            gr.update(value=""),
        )
    
    # Events
    url_input.change(fn=detect_duration, inputs=url_input, outputs=duration_display)
    
    tts_engine_dropdown.change(
        fn=update_omnivoice_controls,
        inputs=tts_engine_dropdown,
        outputs=[voice_mode_radio, voice_instruct_text, clone_row, save_row, edge_voice_dropdown]
    )
    
    voice_mode_radio.change(
        fn=update_voice_mode_controls,
        inputs=voice_mode_radio,
        outputs=[voice_instruct_text, clone_row, save_row]
    )
    
    save_voice_btn.click(
        fn=save_voice,
        inputs=[ref_audio_upload, new_voice_name],
        outputs=[saved_voice_dropdown, new_voice_name]
    )
    
    preview_btn.click(
        fn=preview_voice,
        inputs=[preview_text, tts_engine_dropdown, voice_mode_radio, voice_instruct_text, ref_audio_upload, saved_voice_dropdown],
        outputs=preview_audio
    )
    
    submit_btn.click(
        fn=run_dubbing,
        inputs=[
            url_input, source_lang_dropdown, edge_voice_dropdown, whisper_dropdown,
            orig_volume_slider, dub_volume_slider, bgm_volume_slider, orig_vocal_volume_slider,
            separate_vocals_checkbox, burn_subs_checkbox, sub_style_dropdown,
            tts_engine_dropdown, voice_mode_radio, voice_instruct_text,
            ref_audio_upload, saved_voice_dropdown
        ],
        outputs=[log_output, video_output, subtitled_video_output, subtitle_vi_output, subtitle_zh_output, no_music_output, vocal_output, tts_output, transcript_output]
    )
    
    remix_btn.click(
        fn=run_remix,
        inputs=[
            orig_volume_slider, dub_volume_slider, bgm_volume_slider, orig_vocal_volume_slider,
            burn_subs_checkbox, sub_style_dropdown
        ],
        outputs=[log_output, video_output, subtitled_video_output, subtitle_vi_output, subtitle_zh_output, no_music_output, vocal_output, tts_output, transcript_output]
    )
    
    cancel_btn.click(
        fn=cancel_processing,
        inputs=None,
        outputs=None
    )
    
    # Load saved voices on startup
    app.load(fn=refresh_saved_voices, inputs=None, outputs=saved_voice_dropdown)

if __name__ == "__main__":
    is_colab = "google.colab" in sys.modules or "--share" in sys.argv
    print("🚀 Đang khởi động Gradio server...")
    sys.stdout.flush()
    app.launch(inbrowser=not is_colab, share=is_colab, debug=True)


