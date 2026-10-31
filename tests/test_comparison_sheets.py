import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import subprocess

from PIL import Image
from comparison_sheets import generate_comparison_sheets


class ComparisonTests(unittest.TestCase):
    def test_twenty_videos_sixteen_positions_and_failure_slots(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'input'
            source.mkdir()
            for i in range(20):
                (source / f'{i:02d}.mp4').touch()
            calls = []
            def extract(path, timestamp, destination, size):
                calls.append((path.name, timestamp))
                Image.new('RGB', (64, 32), (int(path.stem) * 10, 0, 0)).save(destination)
            with patch('comparison_sheets._probe', return_value=(10, 0.04)), patch('comparison_sheets._extract', side_effect=extract):
                result = generate_comparison_sheets(source, Path(directory) / 'out', keep_frames=True)
            self.assertEqual(result['extracted_frames'], 320)
            self.assertEqual(len(result['paths']), 16)
            self.assertEqual(len(list(Path(result['output_dir']).rglob('Frame_*.png'))), 320)
            self.assertEqual([name for name, _ in calls[:20]], [f'{i:02d}.mp4' for i in range(20)])
            self.assertEqual([name for name, _ in calls[:20]], [name for name, _ in calls[-20:]])
            self.assertEqual(calls[0][1], 0)
            self.assertAlmostEqual(calls[-1][1], 9.96)
            with Image.open(result['paths'][0]) as first, Image.open(result['paths'][-1]) as last:
                self.assertEqual(first.size, last.size)

    def test_real_mixed_videos_and_corrupt_file(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'input'
            source.mkdir()
            for name, size, duration, rate in [('a.mp4', '160x90', '1', '12'), ('b.mkv', '90x160', '2', '24')]:
                subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                                f'color=red:size={size}:rate={rate}', '-t', duration,
                                '-c:v', 'libx264', str(source / name)], check=True)
            (source / 'c.avi').write_text('corrupt')
            progress = []
            result = generate_comparison_sheets(source, Path(directory) / 'out', frame_count=3,
                                                 sheet_size=(800, 600), image_format='JPEG',
                                                 progress_callback=lambda c, t: progress.append((c, t)))
            self.assertEqual(result['extracted_frames'], 6)
            self.assertEqual(len(result['warnings']), 1)
            self.assertEqual(len(result['paths']), 3)
            self.assertEqual(progress[-1][0], progress[-1][1])
            with Image.open(result['paths'][0]) as sheet:
                self.assertEqual(sheet.size, (800, 600))
            again = generate_comparison_sheets(source, Path(directory) / 'out', frame_count=1)
            self.assertNotEqual(again['output_dir'], result['output_dir'])

    def test_invalid_dimensions_and_empty_input(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                generate_comparison_sheets(directory, directory)
            (Path(directory) / 'a.mov').touch()
            with self.assertRaises(ValueError):
                generate_comparison_sheets(directory, directory, sheet_size=(20, 20))


if __name__ == '__main__':
    unittest.main()
