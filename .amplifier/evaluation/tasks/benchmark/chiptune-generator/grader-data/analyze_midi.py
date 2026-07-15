# Copyright (c) Microsoft. All rights reserved.

"""Standalone MIDI analysis script for chiptune_generator benchmark evaluation.

Parses a MIDI file and prints a JSON report to stdout with musical properties
that can be used to programmatically evaluate generated chip tunes.

Usage:
    python analyze_midi.py <path_to_midi_file>
"""

import json
import subprocess
import sys

try:
    import mido
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "mido"], stdout=subprocess.DEVNULL)
    import mido


def analyze_midi(file_path: str) -> dict:
    mid = mido.MidiFile(file_path)

    tempo_bpm = 120  # MIDI default
    channels_used: set[int] = set()
    note_counts: dict[int, int] = {}
    pitch_ranges: dict[int, list[int]] = {}  # channel -> [min, max]
    velocity_sums: dict[int, float] = {}
    programs: dict[int, int] = {}

    for track in mid.tracks:
        for msg in track:
            if msg.type == "set_tempo":
                tempo_bpm = round(mido.tempo2bpm(msg.tempo))
            elif msg.type == "program_change":
                programs[msg.channel] = msg.program
            elif msg.type == "note_on" and msg.velocity > 0:
                ch = msg.channel
                channels_used.add(ch)
                note_counts[ch] = note_counts.get(ch, 0) + 1
                velocity_sums[ch] = velocity_sums.get(ch, 0.0) + msg.velocity
                if ch not in pitch_ranges:
                    pitch_ranges[ch] = [msg.note, msg.note]
                else:
                    pitch_ranges[ch][0] = min(pitch_ranges[ch][0], msg.note)
                    pitch_ranges[ch][1] = max(pitch_ranges[ch][1], msg.note)

    total_note_count = sum(note_counts.values())
    per_channel_avg_velocity = {
        str(ch): round(velocity_sums[ch] / note_counts[ch], 2) for ch in note_counts if note_counts[ch] > 0
    }

    return {
        "total_duration_seconds": round(mid.length, 2),
        "tempo_bpm": tempo_bpm,
        "num_tracks": len(mid.tracks),
        "channels_used": sorted(channels_used),
        "per_channel_note_count": {str(ch): count for ch, count in sorted(note_counts.items())},
        "per_channel_pitch_range": {str(ch): rng for ch, rng in sorted(pitch_ranges.items())},
        "per_channel_avg_velocity": {str(ch): per_channel_avg_velocity[str(ch)] for ch in sorted(note_counts)},
        "total_note_count": total_note_count,
        "programs_used": {str(ch): prog for ch, prog in sorted(programs.items())},
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <midi_file_path>", file=sys.stderr)
        sys.exit(1)
    result = analyze_midi(sys.argv[1])
    print(json.dumps(result, indent=2))
