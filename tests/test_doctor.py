from fslang import __version__, browsers, doctor


def test_launch_attempts_order(monkeypatch):
    monkeypatch.setattr(browsers, "find_installed", lambda ch: f"/x/{ch}.exe" if ch == "chrome" else None)
    labels = [label for label, _ in browsers.launch_attempts("auto")]
    assert labels == ["Chromium（Playwright）", "Microsoft Edge", "Google Chrome", "Google Chrome（/x/chrome.exe）"]
    assert browsers.launch_attempts("auto")[-1][1] == {"executable_path": "/x/chrome.exe"}
    assert [l for l, _ in browsers.launch_attempts("msedge")] == ["Microsoft Edge"]


def test_doctor_reports_version_and_folder(capsys, monkeypatch):
    # 瀏覽器與網路檢查較慢，這裡只檢查版本、套件、連接埠
    monkeypatch.setattr(doctor, "check_browsers", lambda: None)
    monkeypatch.setattr(doctor, "check_site", lambda: None)
    assert doctor.main() == 0
    out = capsys.readouterr().out
    assert f"程式版本：{__version__}" in out
    assert f"程式資料夾：{doctor.ROOT}" in out
    assert "套件 openpyxl" in out and "連接埠 8000" in out


def test_doctor_keeps_going_when_a_check_breaks(capsys, monkeypatch):
    def broken():
        raise RuntimeError("壞掉了")
    monkeypatch.setattr(doctor, "check_packages", broken)
    monkeypatch.setattr(doctor, "check_browsers", lambda: None)
    monkeypatch.setattr(doctor, "check_site", lambda: None)
    doctor.main()
    out = capsys.readouterr().out
    assert "檢查時發生錯誤：壞掉了" in out and "連接埠 8000" in out
