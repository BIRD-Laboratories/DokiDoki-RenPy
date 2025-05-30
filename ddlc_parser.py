import os
import re
import json
import argparse
from collections import defaultdict, deque

class RenPySceneParser:
    def __init__(self, project_path):
        self.project_path = project_path
        self.scenes = []
        self.character_defs = {}
        self.images = set()
        self.audio = set()
        self.defined_images = set()
        
        # Current state
        self.current_scene = None
        self.current_label = None
        self.current_bg = None
        self.current_sprites = defaultdict(dict)
        self.label_stack = []
        self.visited_labels = set()
        self.in_menu = False
        self.menu_choices = []
        
        # Script files and label index
        self.script_files = []
        self.labels = {}
        self.file_contents = {}
        self.asset_map = {}
        self.label_order = []
        
        # Asset directories
        self.asset_dirs = [
            os.path.join(project_path, 'game'),
            os.path.join(project_path, 'images'),
            os.path.join(project_path, 'audio'),
            os.path.join(project_path, 'gui'),
            os.path.join(project_path, 'bg')
        ]
        
        # Visual change tracking
        self.visual_change = False
        self.label_dependencies = defaultdict(list)
        self.label_entry_points = set()

    def index_assets(self):
        """Index all assets in the project directory"""
        for asset_dir in self.asset_dirs:
            if not os.path.exists(asset_dir):
                continue
                
            for root, _, files in os.walk(asset_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    rel_path = os.path.relpath(file_path, self.project_path).replace('\\', '/')
                    
                    # Add to asset map with and without extension
                    base_name = os.path.splitext(file)[0]
                    self.asset_map[base_name] = rel_path
                    self.asset_map[file] = rel_path

    def resolve_asset(self, asset_ref, asset_type='image'):
        """Resolve an asset reference to its actual path"""
        # Try exact match first
        if asset_ref in self.asset_map:
            return self.asset_map[asset_ref]
        
        # Try without extension
        base_ref = os.path.splitext(asset_ref)[0]
        if base_ref in self.asset_map:
            return self.asset_map[base_ref]
        
        # Try common extensions
        extensions = {
            'image': ['.png', '.jpg', '.jpeg', '.webp'],
            'audio': ['.ogg', '.mp3', '.wav']
        }.get(asset_type, [])
        
        for ext in extensions:
            ref_with_ext = asset_ref + ext
            if ref_with_ext in self.asset_map:
                return self.asset_map[ref_with_ext]
        
        # Check in defined images
        if asset_ref in self.defined_images:
            return f"images/{asset_ref}.png"
        
        # If still not found, return a best-guess path
        return f"images/{asset_ref}.png" if asset_type == 'image' else f"audio/{asset_ref}.ogg"

    def parse_project(self):
        """Parse the Ren'Py project by analyzing all script files"""
        print("Indexing assets...")
        self.index_assets()
        
        print("Discovering script files...")
        script_dir = os.path.join(self.project_path, 'game')
        if not os.path.exists(script_dir):
            script_dir = self.project_path
            
        for root, _, files in os.walk(script_dir):
            for file in files:
                if file.endswith('.rpy'):
                    full_path = os.path.join(root, file)
                    self.script_files.append(full_path)
                    with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                        self.file_contents[full_path] = f.readlines()
        
        # Build label index and dependency graph
        print("Indexing labels and dependencies...")
        for file_path in self.script_files:
            self.index_labels_and_dependencies(file_path)
        
        # Determine starting points
        start_labels = ["start", "main", "begin"]
        entry_point = next((label for label in start_labels if label in self.labels), None)
        if not entry_point:
            entry_point = next((label for label in self.labels if not self.label_dependencies[label]), None)
            if not entry_point:
                entry_point = next(iter(self.labels.keys()), None)
        
        if not entry_point:
            print("Warning: No valid entry point found in the project")
            return
        
        print(f"Starting narrative reconstruction from: {entry_point}")
        self.reconstruct_narrative_sequence(entry_point)
        
        # Parse any remaining labels
        self.parse_unvisited_labels()
        
        # Finalize the last scene
        self.finalize_current_scene()

    def index_labels_and_dependencies(self, file_path):
        """Index all labels and their dependencies in a file"""
        lines = self.file_contents[file_path]
        current_label = None
        start_line = 0
        
        for i, line in enumerate(lines):
            line = line.strip()
            if line.startswith('label '):
                if current_label:
                    self.labels[current_label] = (file_path, start_line, i)
                    self.label_order.append(current_label)
                
                parts = line.split('label ')[1].split(':')
                label_name = parts[0].strip()
                current_label = label_name
                start_line = i
            elif line.startswith(('jump ', 'call ')):
                if current_label:
                    target = self.extract_transfer_target(line)
                    if target:
                        self.label_dependencies[target].append(current_label)
            elif line.startswith('return'):
                if current_label:
                    self.labels[current_label] = (file_path, start_line, i)
                    self.label_order.append(current_label)
                    current_label = None
        
        if current_label:
            self.labels[current_label] = (file_path, start_line, len(lines))
            self.label_order.append(current_label)

    def extract_transfer_target(self, line):
        """Extract target label from jump/call statements"""
        if line.startswith('jump '):
            return line[5:].split('#')[0].strip()
        elif line.startswith('call '):
            return line[5:].split('(')[0].split('#')[0].strip()
        return None

    def reconstruct_narrative_sequence(self, start_label):
        """Reconstruct the narrative sequence using BFS"""
        if start_label not in self.labels:
            print(f"Warning: Starting label '{start_label}' not found")
            return
            
        queue = deque([start_label])
        self.label_entry_points.add(start_label)
        
        while queue:
            label_name = queue.popleft()
            
            if label_name in self.visited_labels:
                continue
                
            if label_name not in self.labels:
                print(f"Warning: Label '{label_name}' not found - skipping")
                continue
                
            self.parse_label(label_name)
            self.visited_labels.add(label_name)
            
            file_path, start_line, end_line = self.labels[label_name]
            lines = self.file_contents[file_path][start_line:end_line]
            
            if lines and lines[0].strip().startswith('label '):
                lines = lines[1:]
            
            for line in lines:
                line = line.strip()
                if line.startswith(('jump ', 'call ')):
                    target = self.extract_transfer_target(line)
                    if target and target not in self.visited_labels:
                        queue.append(target)
                        self.label_entry_points.add(target)

    def parse_unvisited_labels(self):
        """Parse labels not reached through main flow"""
        unvisited = [label for label in self.labels if label not in self.visited_labels]
        print(f"Found {len(unvisited)} unvisited labels")
        
        for label in self.label_order:
            if label in unvisited:
                print(f"Parsing unvisited label: {label}")
                self.parse_label(label)
                self.visited_labels.add(label)

    def parse_label(self, label_name):
        """Parse a label"""
        if label_name not in self.labels:
            print(f"Warning: Label '{label_name}' not found - skipping")
            return
            
        file_path, start_line, end_line = self.labels[label_name]
        self.current_label = label_name
        self.label_stack.append(label_name)
        
        print(f"Parsing label: {label_name} in {os.path.basename(file_path)}")
        
        lines = self.file_contents[file_path][start_line:end_line]
        
        if lines and lines[0].strip().startswith('label '):
            lines = lines[1:]
        
        if label_name in self.label_entry_points:
            self.current_bg = None
            self.current_sprites.clear()
            self.visual_change = True
            self.new_scene()
        
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if not line or line.startswith('#'):
                i += 1
                continue
                
            if self.handle_block_structure(line, lines, i):
                i += 1
                continue
                
            try:
                self.parse_line(line)
            except Exception as e:
                print(f"Error parsing line {start_line + i + 1} in {file_path}: {line}")
                print(f"Error details: {str(e)}")
            
            i += 1
        
        self.label_stack.pop()

    def handle_block_structure(self, line, lines, index):
        """Handle conditional blocks and other structures"""
        if line.startswith('if '):
            end_index = self.find_block_end(lines, index, 'if', 'endif')
            if end_index > index:
                inner_lines = lines[index+1:end_index]
                for j in range(len(inner_lines)):
                    inner_line = inner_lines[j].strip()
                    if inner_line and not inner_line.startswith('#'):
                        self.parse_line(inner_line)
                return True
            return False
            
        elif line.startswith('else:'):
            return True
            
        return False

    def find_block_end(self, lines, start_index, block_start, block_end):
        """Find the end of a code block"""
        depth = 1
        i = start_index + 1
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith(block_start):
                depth += 1
            elif line.startswith(block_end):
                depth -= 1
                if depth == 0:
                    return i
            i += 1
        return len(lines) - 1

    def parse_line(self, line):
        """Parse a single line of Ren'Py script"""
        line = re.sub(r'#.*$', '', line).strip()
        if not line:
            return
            
        if line.startswith('image '):
            self.handle_image_declaration(line)
            return
            
        if line.startswith('define '):
            self.handle_define(line)
            return
            
        if line.startswith(('jump ', 'call ')):
            self.handle_transfer(line)
            return
            
        if line == "return":
            return

        if line.startswith('scene '):
            self.handle_scene(line)
        elif line.startswith('show '):
            self.handle_show(line)
        elif line.startswith('hide '):
            self.handle_hide(line)

        elif '"' in line and not line.startswith(('menu', 'python', '$')):
            self.handle_dialogue(line)

        elif line.startswith('play '):
            self.handle_audio(line)

        if self.in_menu:
            if line.startswith('"') and '"' in line[1:]:
                self.handle_menu_choice(line)
            elif line.startswith(('jump ', 'call ')):
                self.handle_menu_choice_target(line)
        elif line.startswith('menu:'):
            self.handle_menu_start()

    def new_scene(self):
        """Create a new scene frame if needed"""
        if self.current_scene and (
            self.visual_change or
            self.current_scene.get('background') or
            self.current_scene.get('sprites') or
            self.current_scene.get('dialogue') or
            self.current_scene.get('audio') or
            self.current_scene.get('choices')
        ):
            if (self.current_scene['background'] or 
                self.current_scene['sprites'] or 
                self.current_scene['dialogue'] or 
                self.current_scene['audio'] or 
                self.current_scene['choices']):
                self.scenes.append(self.current_scene)
            self.current_scene = None
            self.visual_change = False
        
        if not self.current_scene:
            self.current_scene = {
                'label_path': list(self.label_stack),
                'background': self.current_bg,
                'sprites': dict(self.current_sprites),
                'dialogue': [],
                'audio': [],
                'choices': []
            }

    def finalize_current_scene(self):
        """Finalize the current scene"""
        if self.current_scene:
            self.new_scene()
        self.current_scene = None

    def handle_image_declaration(self, line):
        """Handle image declarations"""
        match = re.match(r'image\s+([^=]+)=(.+)', line)
        if match:
            image_name = match.group(1).strip()
            image_path = match.group(2).strip().strip('"\'')
            self.defined_images.add(image_name)
            resolved_path = self.resolve_asset(image_path, 'image')
            self.images.add(resolved_path)
            print(f"Found image declaration: {image_name} -> {resolved_path}")

    def handle_scene(self, line):
        """Handle scene changes (background)"""
        bg_match = re.match(r'scene\s+(.+?)(?:\s+at\s+(\S+))?$', line)
        if bg_match:
            bg_image = bg_match.group(1).strip()
            resolved_bg = self.resolve_asset(bg_image, 'image')
            self.current_bg = resolved_bg
            self.images.add(resolved_bg)
            self.current_sprites.clear()
            self.visual_change = True
            print(f"Scene change: {resolved_bg}")

    def handle_show(self, line):
        """Handle showing sprites"""
        show_match = re.match(r'show\s+(.+?)(?:\s+at\s+(\S+))?$', line)
        if show_match:
            sprite_ref = show_match.group(1).strip()
            position = show_match.group(2) or 'center'
            resolved_sprite = self.resolve_asset(sprite_ref, 'image')
            self.current_sprites[position] = {
                'image': resolved_sprite,
                'transform': position
            }
            self.images.add(resolved_sprite)
            self.visual_change = True
            print(f"Show sprite: {resolved_sprite} at {position}")

    def handle_hide(self, line):
        """Handle hiding sprites"""
        hide_match = re.match(r'hide\s+(.+?)$', line)
        if hide_match:
            sprite_ref = hide_match.group(1).strip()
            resolved_sprite = self.resolve_asset(sprite_ref, 'image')
            for pos in list(self.current_sprites.keys()):
                if self.current_sprites[pos].get('image') == resolved_sprite:
                    del self.current_sprites[pos]
            self.visual_change = True
            print(f"Hide sprite: {resolved_sprite}")

    def handle_dialogue(self, line):
        """Handle dialogue lines"""
        char_match = re.match(r'^([a-zA-Z_]+)\s*"(.+)"', line)
        if char_match:
            speaker_var, text = char_match.groups()
            speaker = self.character_defs.get(speaker_var, speaker_var)
            self.new_scene()
            self.current_scene['dialogue'].append({
                'speaker': speaker,
                'text': text
            })
            print(f"Dialogue: {speaker}: {text}")
        else:
            text_match = re.match(r'^"(.+)"', line)
            if text_match:
                self.new_scene()
                self.current_scene['dialogue'].append({
                    'speaker': None,
                    'text': text_match.group(1)
                })
                print(f"Narration: {text_match.group(1)}")

    def handle_define(self, line):
        """Handle character definitions"""
        define_match = re.match(r'define\s+([^\s=]+)\s*=\s*(.+)', line)
        if define_match:
            var_name = define_match.group(1)
            value = define_match.group(2).strip(' "\'')
            if var_name.endswith('_name'):
                self.character_defs[var_name] = value
                print(f"Character definition: {var_name} = {value}")
            elif var_name.startswith('images.'):
                self.defined_images.add(var_name.split('.', 1)[1])

    def handle_audio(self, line):
        """Handle audio playback"""
        audio_match = re.search(r'["\']([^"\']+\.(?:mp3|ogg|wav))["\']', line)
        if not audio_match:
            audio_match = re.search(r'["\']([^"\']+)["\']', line)
            
        if audio_match:
            audio_ref = audio_match.group(1)
            resolved_audio = self.resolve_asset(audio_ref, 'audio')
            self.new_scene()
            self.current_scene['audio'].append(resolved_audio)
            self.audio.add(resolved_audio)
            print(f"Audio: {resolved_audio}")

    def handle_menu_start(self):
        """Handle menu start"""
        self.in_menu = True
        self.menu_choices = []
        self.new_scene()
        print("Menu start")

    def handle_menu_choice(self, line):
        """Handle menu choices"""
        choice_match = re.match(r'^\s*"([^"]+)"', line)
        if choice_match:
            choice_text = choice_match.group(1)
            self.menu_choices.append({
                'text': choice_text,
                'target': None
            })
            print(f"Menu choice: {choice_text}")

    def handle_menu_choice_target(self, line):
        """Handle jump/call after menu choice"""
        if self.menu_choices:
            if line.startswith('jump '):
                target = line[5:].split('#')[0].strip()
                self.menu_choices[-1]['target'] = target
            elif line.startswith('call '):
                target = line[5:].split('(')[0].split('#')[0].strip()
                self.menu_choices[-1]['target'] = target
            
            self.in_menu = False
            self.new_scene()
            self.current_scene['choices'] = list(self.menu_choices)
            self.menu_choices = []

    def handle_transfer(self, line):
        """Handle jumps and calls"""
        if line.startswith('jump '):
            target = line[5:].split('#')[0].strip()
            if self.menu_choices:
                self.menu_choices[-1]['target'] = target
                self.new_scene()
                self.current_scene['choices'] = list(self.menu_choices)
                self.menu_choices = []
            
            if target in self.labels:
                self.parse_label(target)
            else:
                print(f"Warning: Jump target '{target}' not found - skipping")
        elif line.startswith('call '):
            target = line[5:].split('(')[0].split('#')[0].strip()
            if target in self.labels:
                self.parse_label(target)
            else:
                print(f"Warning: Call target '{target}' not found - skipping")

    def finalize_scenes(self):
        """Finalize the scene list"""
        self.finalize_current_scene()
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

    print(f"Starting parser for project: {args.project_path}")
    parser = RenPySceneParser(args.project_path)
    parser.parse_project()
    result = parser.finalize_scenes()
    
    print(f"Parsed {len(result['scenes'])} scenes")
    print(f"Found {len(result['characters'])} characters")
    print(f"Found {len(result['images'])} images")
    print(f"Found {len(result['audio'])} audio files")

    with open(args.output_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"Successfully generated scene list: {args.output_file}")
