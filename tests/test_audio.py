import subprocess

from helpers import write_tone_wav

from voicecut import audio


def test_probe_silence_render_audio(tmp_path):
    src = write_tone_wav(tmp_path / "in.wav", [(1, False), (1, True), (2, False), (1, True), (1, False)])
    info = audio.probe(src)
    assert abs(info.duration - 6.0) < 0.05 and not info.has_video and info.has_audio
    sil = audio.detect_silences(src, noise_db=-35, min_duration=0.5, total_duration=info.duration)
    assert len(sil) == 3
    assert abs(sil[0][0] - 0.0) < 0.05 and abs(sil[0][1] - 1.0) < 0.1
    assert abs(sil[1][0] - 2.0) < 0.1 and abs(sil[1][1] - 4.0) < 0.1
    assert abs(sil[2][0] - 5.0) < 0.1 and abs(sil[2][1] - 6.0) < 0.05
    out = tmp_path / "out.wav"
    progress = []
    audio.render(src, out, [(1.0, 2.0), (4.0, 5.0)], has_video=False, progress_cb=progress.append)
    assert abs(audio.probe(out).duration - 2.0) < 0.05 and progress[-1] == 1.0
    prev = audio.make_preview(src, tmp_path / "p.mp3")
    assert prev.exists() and audio.probe(prev).duration > 5.5
    out_mp3 = tmp_path / "out.mp3"
    audio.render(src, out_mp3, [(1.0, 2.0)], has_video=False)
    assert abs(audio.probe(out_mp3).duration - 1.0) < 0.15


def test_render_video(tmp_path):
    ff = audio.find_ffmpeg()
    src = tmp_path / "in.mp4"
    subprocess.run([
        ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10:duration=4",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(src),
    ], check=True)
    info = audio.probe(src)
    assert info.has_video and abs(info.duration - 4.0) < 0.2
    out = tmp_path / "out.mp4"
    audio.render(src, out, [(0.5, 1.5), (2.5, 3.5)], has_video=True)
    o = audio.probe(out)
    assert o.has_video and abs(o.duration - 2.0) < 0.2
    out2 = tmp_path / "out.m4a"
    audio.render(src, out2, [(0.5, 1.5)], has_video=True, options=audio.RenderOptions(audio_only=True))
    o2 = audio.probe(out2)
    assert not o2.has_video and abs(o2.duration - 1.0) < 0.2


def test_output_extension():
    assert audio.output_extension("a.mov", True, False) == ".mp4"
    assert audio.output_extension("a.mov", True, True) == ".m4a"
    assert audio.output_extension("a.mp3", False, False) == ".mp3"
    assert audio.output_extension("a.xyz", False, False) == ".m4a"


def test_filter_script():
    s = audio.build_filter_script([(0.0, 1.0), (2.0, 2.006)], with_video=True, fade=0.008)
    assert s.count("[0:v]trim") == 2 and "concat=n=2:v=1:a=1[outv][outa]" in s
    assert "afade=t=in:st=0:d=0.008" in s and "afade=t=in:st=0:d=0.003" in s  # 짧은 조각은 페이드를 줄임
