import os
import re
import json
import argparse
from collections import defaultdict

class RenPySceneParser:
    def __init__(self, project_path):
        self.project_path = project_path
        self.character_defs = {}
        self.images = set()
        self.audio = set()
        self.defined_images = set()
        
        # Current state
        self.current_scene = None
        self.current_label = None
        self.current_bg = None
        self.current_sprites = defaultdict(dict)
        
        # Script files and label index
        self.script_files = []
        self.labels = {}
        self.file_contents = {}
        self.asset_map = {}
        
        # Asset directories
        self.asset_dirs = [
            os.path.join(project_path, 'game'),
            os.path.join(project_path, 'images'),
            os.path.join(project_path, 'audio'),
        ]
        
        # Tracking
        self.errors = []
        self.scene_count = 0

    def log_error(self, message):
        """Log an error message"""
        self.errors.append(message)

    def index_assets(self):
        """Index all assets in the project directory"""
        for asset_dir in self.asset_dirs:
            if not os.path.exists(asset_dir):
                continue
                
            for root, _, files in os.walk(asset_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    rel_path = os.path.relpath(file_path, self.project_path).replace('\\', '/')
                    base_name = os.path.splitext(file)[0]
                    self.asset_map[base_name] = rel_path
                    self.asset_map[file] = rel_path

    def resolve_asset(self, asset_ref, asset_type='image'):
        """Resolve an asset reference to its actual path"""
        if asset_ref in self.asset_map:
            return self.asset_map[asset_ref]
        
        base_ref = os.path.splitext(asset_ref)[0]
        if base_ref in self.asset_map:
            return self.asset_map[base_ref]
        
        extensions = {
            'image': ['.png', '.jpg', '.jpeg', '.webp'],
            'audio': ['.ogg', '.mp3', '.wav']
        }.get(asset_type, [])
        
        for ext in extensions:
            ref_with_ext = asset_ref + ext
            if ref_with_ext in self.asset_map:
                return self.asset_map[ref_with_ext]
        
        if asset_ref in self.defined_images:
            return f"images/{asset_ref}.png"
        
        return asset_ref

    def parse_project(self, output_file):
        """Parse the Ren'Py project and stream results to output file"""
        try:
            # Initial setup
            self.index_assets()
            self.discover_script_files()
            self.index_labels()
            
            # Parse all labels in the order they appear
            for label_name in self.get_label_order():
                self.parse_label(label_name, output_file)
            
        except Exception as e:
            self.log_error(f"Critical error: {str(e)}")

    def discover_script_files(self):
        """Discover all script files in the project"""
        script_dir = os.path.join(self.project_path, 'game')
        if not os.path.exists(script_dir):
            script_dir = self.project_path
            
        for root, _, files in os.walk(script_dir):
            for file in files:
                if file.endswith('.rpy'):
                    full_path = os.path.join(root, file)
                    self.script_files.append(full_path)
                    try:
                        with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                            self.file_contents[full_path] = f.readlines()
                    except Exception as e:
                        self.log_error(f"Error reading {full_path}: {str(e)}")

    def index_labels(self):
        """Index all labels across all script files"""
        for file_path in self.script_files:
            if file_path not in self.file_contents:
                continue
                
            lines = self.file_contents[file_path]
            current_label = None
            start_line = 0
            
            for i, line in enumerate(lines):
                line = line.strip()
                if line.startswith('label '):
                    if current_label:
                        self.labels[current_label] = (file_path, start_line, i)
                    
                    parts = line.split('label ')[1].split(':')
                    label_name = parts[0].strip()
                    current_label = label_name
                    start_line = i
                elif line.startswith('return'):
                    if current_label:
                        self.labels[current_label] = (file_path, start_line, i)
                        current_label = None
        
            if current_label:
                self.labels[current_label] = (file_path, start_line, len(lines))

    def get_label_order(self):
        """Get labels in the order they appear in files"""
        label_order = []
        for file_path in self.script_files:
            if file_path not in self.file_contents:
                continue
                
            lines = self.file_contents[file_path]
            for line in lines:
                line = line.strip()
                if line.startswith('label '):
                    parts = line.split('label ')[1].split(':')
                    label_name = parts[0].strip()
                    if label_name in self.labels:
                        label_order.append(label_name)
        return label_order

    def parse_label(self, label_name, output_file):
        """Parse a single label"""
        if label_name not in self.labels:
            self.log_error(f"Label '{label_name}' not found")
            return
            
        self.current_label = label_name
        
        file_path, start_line, end_line = self.labels[label_name]
        lines = self.file_contents[file_path][start_line:end_line]
        
        # Skip label definition line
        if lines and lines[0].strip().startswith('label '):
            lines = lines[1:]
        
        # Reset visual state for new label
        self.current_bg = None
        self.current_sprites.clear()
        self.new_scene()
        
        # Parse each line
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
                
            try:
                self.parse_line(line, output_file)
            except Exception as e:
                self.log_error(f"Error parsing line: {line}\n{str(e)}")
        
        # Finalize the last scene in the label
        self.finalize_current_scene(output_file)

    def parse_line(self, line, output_file):
        """Parse a single line of Ren'Py script"""
        # Skip complex structures
        if any(line.startswith(x) for x in ['if ', 'else', 'menu', 'python', '$', 'jump ', 'call ']):
            return
            
        # Handle basic Ren'Py commands
        if line.startswith('image '):
            self.handle_image_declaration(line)
        elif line.startswith('define '):
            self.handle_define(line)
        elif line.startswith('scene '):
            self.handle_scene(line, output_file)
        elif line.startswith('show '):
            self.handle_show(line, output_file)
        elif line.startswith('hide '):
            self.handle_hide(line, output_file)
        elif line.startswith('play '):
            self.handle_audio(line)
        elif '"' in line:  # Dialogue
            self.handle_dialogue(line)

    def new_scene(self):
        """Create a new scene frame"""
        self.current_scene = {
            'label': self.current_label,
            'background': self.current_bg,
            'sprites': dict(self.current_sprites),
            'dialogue': [],
            'audio': []
        }

    def finalize_current_scene(self, output_file):
        """Finalize and write the current scene"""
        if self.current_scene and (
            self.current_scene['background'] or 
            self.current_scene['sprites'] or 
            self.current_scene['dialogue'] or 
            self.current_scene['audio']
        ):
            self.write_scene(output_file, self.current_scene)
            self.scene_count += 1
        self.current_scene = None

    def write_scene(self, output_file, scene):
        """Write a scene to the output file"""
        try:
            json.dump(scene, output_file)
            output_file.write('\n')
        except Exception as e:
            self.log_error(f"Error writing scene: {str(e)}")

    def handle_image_declaration(self, line):
        """Handle image declarations"""
        parts = line.split('=', 1)
        if len(parts) > 1:
            image_name = parts[0].replace('image', '').strip()
            image_path = parts[1].strip().strip('"\'')
            self.defined_images.add(image_name)
            resolved_path = self.resolve_asset(image_path, 'image')
            self.images.add(resolved_path)

    def handle_scene(self, line, output_file):
        """Handle scene changes (background)"""
        bg_match = re.match(r'scene\s+(.+)', line)
        if bg_match:
            bg_image = bg_match.group(1).strip()
            resolved_bg = self.resolve_asset(bg_image, 'image')
            self.current_bg = resolved_bg
            self.images.add(resolved_bg)
            self.current_sprites.clear()
            self.finalize_current_scene(output_file)
            self.new_scene()

    def handle_show(self, line, output_file):
        """Handle showing sprites"""
        show_match = re.match(r'show\s+(.+)', line)
        if show_match:
            sprite_ref = show_match.group(1).strip()
            resolved_sprite = self.resolve_asset(sprite_ref, 'image')
            self.current_sprites[sprite_ref] = resolved_sprite
            self.images.add(resolved_sprite)
            self.finalize_current_scene(output_file)
            self.new_scene()

    def handle_hide(self, line, output_file):
        """Handle hiding sprites"""
        hide_match = re.match(r'hide\s+(.+)', line)
        if hide_match:
            sprite_ref = hide_match.group(1).strip()
            resolved_sprite = self.resolve_asset(sprite_ref, 'image')
            if sprite_ref in self.current_sprites:
                del self.current_sprites[sprite_ref]
            self.finalize_current_scene(output_file)
            self.new_scene()

    def handle_dialogue(self, line):
        """Handle dialogue lines"""
        # Extract speaker and text
        char_match = re.match(r'^([a-zA-Z_]+)\s*"(.+)"', line)
        if char_match:
            speaker_var, text = char_match.groups()
            speaker = self.character_defs.get(speaker_var, speaker_var)
            self.current_scene['dialogue'].append({
                'speaker': speaker,
                'text': text
            })
        else:
            text_match = re.match(r'^"(.+)"', line)
            if text_match:
                self.current_scene['dialogue'].append({
                    'speaker': None,
                    'text': text_match.group(1)
                })

    def handle_define(self, line):
        """Handle character definitions"""
        define_match = re.match(r'define\s+([^\s=]+)\s*=\s*(.+)', line)
        if define_match:
            var_name = define_match.group(1)
            value = define_match.group(2).strip(' "\'')
            if var_name.endswith('_name'):
                self.character_defs[var_name] = value

    def handle_audio(self, line):
        """Handle audio playback"""
        audio_match = re.search(r'["\']([^"\']+\.(?:mp3|ogg|wav))["\']', line)
        if audio_match:
            audio_ref = audio_match.group(1)
            resolved_audio = self.resolve_asset(audio_ref, 'audio')
            self.current_scene['audio'].append(resolved_audio)
            self.audio.add(resolved_audio)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert Ren'Py project to JSON scene list")
    parser.add_argument("project_path", help="Path to Ren'Py project directory")
    parser.add_argument("output_file", help="Output JSON file path")
    args = parser.parse_args()

    if not os.path.exists(args.project_path):
        print(f"Error: Project directory not found at {args.project_path}")
        exit(1)

    parser = RenPySceneParser(args.project_path)
    
    try:
        with open(args.output_file, 'w', encoding='utf-8') as f:
            # Parse and stream scenes
            parser.parse_project(f)
            
            # Write metadata at the end
            metadata = {
                'characters': parser.character_defs,
                'images': sorted(parser.images),
                'audio': sorted(parser.audio),
                'scenes_parsed': parser.scene_count
            }
            json.dump(metadata, f, ensure_ascii=False)
            f.write('\n')
            
    except Exception as e:
        parser.log_error(f"Output file error: {str(e)}")

    # Print errors and stats
    if parser.errors:
        print("\nErrors encountered:")
        for error in parser.errors:
            print(f"- {error}")
    else:
        print("\nParsing completed without errors")
        
    print("\nParsing stats:")
    print(f"- Scenes: {parser.scene_count}")
    print(f"- Characters: {len(parser.character_defs)}")
    print(f"- Images: {len(parser.images)}")
    print(f"- Audio files: {len(parser.audio)}")
    print(f"- Labels parsed: {len(parser.labels)}")
