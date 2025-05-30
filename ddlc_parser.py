import os
import re
import json
import argparse
from collections import defaultdict

class RenPySceneParser:
    def __init__(self, project_path):
        self.project_path = project_path
        self.scenes = []
        self.current_scene = None
        self.character_defs = {}
        self.images = set()
        self.audio = set()
        self.label_stack = []
        self.visited_labels = set()
        self.current_bg = None
        self.current_sprites = defaultdict(dict)  # {position: {image: transform}}

    def parse_project(self):
        """Parse all .rpy files in the project directory"""
        for root, _, files in os.walk(self.project_path):
            for file in files:
                if file.endswith('.rpy'):
                    self.parse_file(os.path.join(root, file))

    def parse_file(self, file_path):
        """Parse an individual .rpy file"""
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
            for line in lines:
                self.parse_line(line.strip())

    def parse_line(self, line):
        """Parse a single line of Ren'Py script"""
        if not line or line.startswith('#'):
            return

        # Label definition
        if line.startswith('label '):
            label_name = line.split('label ')[1].split(':')[0].strip()
            self.handle_label(label_name)
            return

        # Skip if not in a label
        if not self.label_stack:
            return

        # Scene/show statements (visual changes)
        if line.startswith('scene '):
            self.handle_scene(line)
        elif line.startswith('show '):
            self.handle_show(line)
        elif line.startswith('hide '):
            self.handle_hide(line)

        # Dialogue
        elif '"' in line and not line.startswith(('menu', 'python', '$')):
            self.handle_dialogue(line)

        # Character definitions
        elif line.startswith('define '):
            self.handle_define(line)

        # Audio
        elif line.startswith('play '):
            self.handle_audio(line)

        # Choices
        elif line.startswith('menu:'):
            self.handle_menu_start()
        elif line.startswith('"') and '"' in line[1:]:
            if self.in_menu:
                self.handle_menu_choice(line)

        # Jumps and calls
        elif line.startswith(('jump ', 'call ')):
            self.handle_transfer(line)

    def handle_label(self, label_name):
        """Handle entering a new label"""
        if not self.label_stack:  # New scene
            self.new_scene()
        self.label_stack.append(label_name)
        self.visited_labels.add(label_name)

    def new_scene(self):
        """Create a new scene frame"""
        if self.current_scene:
            self.scenes.append(self.current_scene)
        self.current_scene = {
            'label_path': [],
            'background': self.current_bg,
            'sprites': dict(self.current_sprites),
            'dialogue': [],
            'audio': [],
            'choices': []
        }

    def handle_scene(self, line):
        """Handle scene changes (background)"""
        bg_match = re.match(r'scene\s+([^\s]+)(?:\s+at\s+([^\s]+))?', line)
        if bg_match:
            bg_image = bg_match.group(1)
            if not bg_image.endswith(('.png', '.jpg', '.jpeg', '.webp')):
                bg_image += '.png'
            
            self.current_bg = bg_image
            self.images.add(bg_image)
            self.current_sprites.clear()  # Clear sprites on scene change
            self.new_scene()

    def handle_show(self, line):
        """Handle showing sprites"""
        show_match = re.match(r'show\s+([^\s]+)(?:\s+at\s+([^\s]+))?', line)
        if show_match:
            sprite_image = show_match.group(1)
            position = show_match.group(2) or 'center'
            
            if not sprite_image.endswith(('.png', '.jpg', '.jpeg', '.webp')):
                sprite_image += '.png'
            
            self.current_sprites[position] = {
                'image': sprite_image,
                'transform': position
            }
            self.images.add(sprite_image)
            self.new_scene()

    def handle_hide(self, line):
        """Handle hiding sprites"""
        hide_match = re.match(r'hide\s+([^\s]+)', line)
        if hide_match:
            sprite_name = hide_match.group(1)
            if not sprite_name.endswith(('.png', '.jpg', '.jpeg', '.webp')):
                sprite_name += '.png'
            
            # Remove sprite from all positions
            for pos in list(self.current_sprites.keys()):
                if self.current_sprites[pos].get('image') == sprite_name:
                    del self.current_sprites[pos]
            self.new_scene()

    def handle_dialogue(self, line):
        """Handle dialogue lines"""
        # Character dialogue
        char_match = re.match(r'^([a-zA-Z_]+)\s*"(.+)"', line)
        if char_match:
            speaker_var, text = char_match.groups()
            speaker = self.character_defs.get(speaker_var, speaker_var)
            self.current_scene['dialogue'].append({
                'speaker': speaker,
                'text': text
            })
            self.new_scene()
        else:
            # Narrator dialogue
            text_match = re.match(r'^"(.+)"', line)
            if text_match:
                self.current_scene['dialogue'].append({
                    'speaker': None,
                    'text': text_match.group(1)
                })
                self.new_scene()

    def handle_define(self, line):
        """Handle character definitions"""
        define_match = re.match(r'define\s+([^\s=]+)\s*=\s*(.+)', line)
        if define_match and define_match.group(1).endswith('_name'):
            var_name = define_match.group(1)
            char_name = define_match.group(2).strip(' "\'')
            self.character_defs[var_name] = char_name

    def handle_audio(self, line):
        """Handle audio playback"""
        audio_match = re.search(r'["\']([^"\']+\.(?:mp3|ogg|wav))["\']', line)
        if audio_match:
            audio_file = audio_match.group(1)
            self.current_scene['audio'].append(audio_file)
            self.audio.add(audio_file)

    def handle_menu_start(self):
        """Handle menu start"""
        self.in_menu = True
        self.current_scene['choices'] = []

    def handle_menu_choice(self, line):
        """Handle menu choices"""
        choice_text = line.strip('"')
        self.current_scene['choices'].append({
            'text': choice_text,
            'target': None  # Will be filled by jump statement
        })

    def handle_transfer(self, line):
        """Handle jumps and calls"""
        if line.startswith('jump '):
            target = line[5:].strip()
            if self.current_scene['choices']:
                # Associate last choice with jump target
                self.current_scene['choices'][-1]['target'] = target
            else:
                # Regular jump - end current scene
                self.new_scene()
        elif line.startswith('call '):
            target = line[5:].split('(')[0].strip()
            # Calls are treated as continuing the same scene

    def finalize_scenes(self):
        """Finalize the scene list"""
        if self.current_scene:
            self.scenes.append(self.current_scene)
        return {
            'characters': self.character_defs,
            'images': sorted(self.images),
            'audio': sorted(self.audio),
            'scenes': self.scenes
        }

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert Ren'Py project to JSON scene list")
    parser.add_argument("project_path", help="Path to Ren'Py project directory")
    parser.add_argument("output_file", help="Output JSON file path")
    args = parser.parse_args()

    if not os.path.exists(args.project_path):
        print(f"Error: Project directory not found at {args.project_path}")
        exit(1)

    parser = RenPySceneParser(args.project_path)
    parser.parse_project()
    result = parser.finalize_scenes()

    with open(args.output_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"Successfully generated scene list: {args.output_file}")
