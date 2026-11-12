# Windows SAPI(ko-KR Heami)로 한 발언을 wav(16kHz mono 16bit)로 합성한다. make_tts_fixture.py가 호출한다.
param([string]$TextFile, [string]$OutWav, [string]$Pitch = "0%", [string]$Rate = "0")
Add-Type -AssemblyName System.Speech
$text = [System.IO.File]::ReadAllText($TextFile, [System.Text.Encoding]::UTF8)
$text = [System.Security.SecurityElement]::Escape($text)
$ssml = "<speak version='1.0' xml:lang='ko-KR' xmlns='http://www.w3.org/2001/10/synthesis'><voice name='Microsoft Heami Desktop'><prosody pitch='$Pitch' rate='$Rate'>$text</prosody></voice></speak>"
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$s.SetOutputToWaveFile($OutWav, $fmt)
$s.SpeakSsml($ssml)
$s.Dispose()
