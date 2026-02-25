import os
import subprocess

# ====== 用户参数 ======
OUTPUT_DIR = "/Users/jyimac/code/ascii_choir/sound_library/guitar_electric"  # TODO 改成你的目标目录
MSCORE_PATH = "/Applications/MuseScore 4.app/Contents/MacOS/mscore" # TODO 改成 MuseScore 可执行文件路径
TEMPO = 80
MIDI_START = 21
MIDI_END = 108
# ======================

os.makedirs(OUTPUT_DIR, exist_ok=True)

def midi_to_pitch(midi):
    octave = (midi // 12) - 1
    note_index = midi % 12
    steps = ["C", "C", "D", "D", "E", "F", "F", "G", "G", "A", "A", "B"]
    alters = [0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0]
    step = steps[note_index]
    alter = alters[note_index]
    return step, alter, octave

def create_musicxml(midi):
    step, alter, octave = midi_to_pitch(midi)

    alter_tag = f"<alter>{alter}</alter>" if alter == 1 else ""
    # TODO 对于不同乐器：修改 part-name、instrument-name、midi-program（电吉他是 26，电贝斯是 33）
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE score-partwise PUBLIC
  "-//Recordare//DTD MusicXML 3.1 Partwise//EN"
  "http://www.musicxml.org/dtds/partwise.dtd">
<score-partwise version="3.1">
  <part-list>
    <score-part id="P1">
      <part-name>Electric Guitar</part-name>
      <score-instrument id="P1-I1">
        <instrument-name>Electric Guitar</instrument-name>
      </score-instrument>
      <midi-instrument id="P1-I1">
        <midi-channel>1</midi-channel>
        <midi-program>26</midi-program>
      </midi-instrument>
    </score-part>
  </part-list>

  <part id="P1">
    <measure number="1">
      <attributes>
        <divisions>1</divisions>
        <key><fifths>0</fifths></key>
        <time>
          <beats>4</beats>
          <beat-type>4</beat-type>
        </time>
        <clef>
          <sign>G</sign>
          <line>2</line>
        </clef>
      </attributes>

      <direction placement="above">
        <direction-type>
          <metronome>
            <beat-unit>quarter</beat-unit>
            <per-minute>{TEMPO}</per-minute>
          </metronome>
        </direction-type>
        <sound tempo="{TEMPO}"/>
      </direction>

      <note>
        <pitch>
          <step>{step}</step>
          {alter_tag}
          <octave>{octave}</octave>
        </pitch>
        <duration>4</duration>
        <type>whole</type>
      </note>
    </measure>
  </part>
</score-partwise>
'''
    return xml

for midi in range(MIDI_START, MIDI_END + 1):
    pitch_str = f"{midi:03d}"
    xml_path = os.path.join(OUTPUT_DIR, f"temp_{pitch_str}.musicxml")
    wav_path = os.path.join(OUTPUT_DIR, f"German Concert D {pitch_str} 083.wav")

    with open(xml_path, "w") as f:
        f.write(create_musicxml(midi))

    subprocess.run([MSCORE_PATH, xml_path, "-o", wav_path])

    os.remove(xml_path)

print("✅ 全部生成完成")