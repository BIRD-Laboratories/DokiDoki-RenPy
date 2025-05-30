import os
import re
import json
import argparse
import sys
from collections import defaultdict, deque

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
        
        # Loop detection
        self.max_label_visits = 3
        self.label_visit_counts = defaultdict(int)
        self.current_path = []
        self.errors = []

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
        try:
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
            
            return f"images/{asset_ref}.png" if asset_type == 'image' else f"audio/{asset_ref}.ogg"
        except Exception as e:
            self.log_error(f"Error resolving asset {asset_ref}: {str(e)}")
            return asset_ref

    def parse_project(self, output_file):
        """Parse the Ren'Py project and stream results to output file"""
        try:
            self.index_assets()
            self.discover_script_files()
            self.index_all_labels()
            
            entry_point = self.find_entry_point()
            if not entry_point:
                raise RuntimeError("No valid entry point found")
                
            self.parse_label_iterative(entry_point)
            self.parse_unvisited_labels()
            self.finalize_current_scene(output_file)
        except Exception as e:
            self.log_error(f"Critical error during parsing: {str(e)}")

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

    def index_all_labels(self):
        """Index all labels across all script files"""
        for file_path in self.script_files:
            self.index_labels(file_path)

    def index_labels(self, file_path):
        """Index labels in a single file"""
        if file_path not in self.file_contents:
            return
            
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
            elif line.startswith('return'):
                if current_label:
                    self.labels[current_label] = (file_path, start_line, i)
                    self.label_order.append(current_label)
                    current_label = None
        
        if current_label:
            self.labels[current_label] = (file_path, start_line, len(lines))
            self.label_order.append(current_label)

    def find_entry_point(self):
        """Find the entry point for parsing"""
        start_labels = ["start", "main", "begin"]
        entry_point = next((label for label in start_labels if label in self.labels), None)
        
        if not entry_point:
            called_labels = set()
            for label in self.labels:
                file_path, start, end = self.labels[label]
                if file_path not in self.file_contents:
                    continue
                    
                for line in self.file_contents[file_path][start:end]:
                    if line.strip().startswith(('jump ', 'call ')):
                        target = self.extract_transfer_target(line.strip())
                        if target:
                            called_labels.add(target)
            
            entry_point = next((label for label in self.labels if label not in called_labels), None)
        
        return entry_point

    def extract_transfer_target(self, line):
        """Extract target label from jump/call statements"""
        try:
            if line.startswith('jump '):
                return line[5:].split('#')[0].strip()
            elif line.startswith('call '):
                return line[5:].split('(')[0].split('#')[0].strip()
            return None
        except:
            return None

    def parse_label_iterative(self, start_label):
        """Parse labels iteratively using a stack"""
        if start_label not in self.labels:
            self.log_error(f"Label '{start_label}' not found")
            return
        
        stack = [(start_label, False)]
        
        while stack:
            label_name, is_continuation = stack.pop()
            
            if self.label_visit_counts[label_name] >= self.max_label_visits:
                self.log_error(f"Maximum visits reached for label '{label_name}'")
                continue
                
            self.label_visit_counts[label_name] += 1
            self.current_label = label_name
            self.label_stack.append(label_name)
            
            if label_name in self.current_path:
                self.log_error(f"Loop detected in label path: {' -> '.join(self.current_path + [label_name])}")
                self.label_stack.pop()
                continue
                
            self.current_path.append(label_name)
            
            try:
                file_path, start_line, end_line = self.labels[label_name]
                lines = self.file_contents[file_path][start_line:end_line]
                
                if not is_continuation and lines and lines[0].strip().startswith('label '):
                    lines = lines[1:]
                
                if len(self.label_stack) == 1:
                    self.current_bg = None
                    self.current_sprites.clear()
                    self.new_scene()
                
                # Parse lines in reverse order
                for i in range(len(lines)-1, -1, -1):
                    line = lines[i].strip()
                    if not line or line.startswith('#'):
                        continue
                        
                    if line.startswith('if '):
                        end_index = self.find_block_end(lines, i, 'if', 'endif')
                        if end_index > i:
                            for j in range(end_index, i, -1):
                                inner_line = lines[j].strip()
                                if inner_line and not inner_line.startswith('#'):
                                    stack.append((label_name, True))
                                    break
                            continue
                    elif line.startswith('else:'):
                        continue
                        
                    stack.append((label_name, True))
                
                # Process lines normally
                for line in lines:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    self.parse_line(line)
            except Exception as e:
                self.log_error(f"Error parsing label '{label_name}': {str(e)}")
            
            self.label_stack.pop()
            self.current_path.pop()

    def parse_unvisited_labels(self):
        """Parse labels not reached through main flow"""
        unvisited = [label for label in self.labels if self.label_visit_counts[label] == 0]
        for label in self.label_order:
            if label in unvisited:
                self.parse_label_iterative(label)

    def find_block_end(self, lines, start_index, block_start, block_end):
        """Find the end of a code block"""
        try:
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
        except:
            return len(lines) - 1

    def parse_line(self, line):
        """Parse a single line of Ren'Py script"""
        try:
            line = re.sub(r'#.*$', '', line).strip()
            if not line:
                return
                
            if line.startswith('image '):
                self.handle_image_declaration(line)
            elif line.startswith('define '):
                self.handle_define(line)
            elif line.startswith(('jump ', 'call ')):
                self.handle_transfer(line)
            elif line == "return":
                return
            elif line.startswith('scene '):
                self.handle_scene(line)
            elif line.startswith('show '):
                self.handle_show(line)
            elif line.startswith('hide '):
                self.handle_hide(line)
            elif '"' in line and not line.startswith(('menu', 'python', '$')):
                self.handle_dialogue(line)
            elif line.startswith('play '):
                self.handle_audio(line)
            elif self.in_menu:
                if line.startswith('"') and '"' in line[1:]:
                    self.handle_menu_choice(line)
                elif line.startswith(('jump ', 'call ')):
                    self.handle_menu_choice_target(line)
            elif line.startswith('menu:'):
                self.handle_menu_start()
        except Exception as e:
            self.log_error(f"Error parsing line: {line}\n{str(e)}")

    def new_scene(self):
        """Create a new scene frame if needed"""
        if self.current_scene and (
            self.current_bg != self.current_scene.get('background') or
            self.current_sprites != self.current_scene.get('sprites') or
            bool(self.current_scene.get('dialogue')) or
            bool(self.current_scene.get('audio')) or
            bool(self.current_scene.get('choices'))
        ):
            self.current_scene = None
        
        if not self.current_scene:
            self.current_scene = {
                'label_path': list(self.label_stack),
                'background': self.current_bg,
                'sprites': dict(self.current_sprites),
                'dialogue': [],
                'audio': [],
                'choices': []
            }

    def finalize_current_scene(self, output_file):
        """Finalize and write the current scene"""
        if self.current_scene:
            self.write_scene(output_file, self.current_scene)
        self.current_scene = None

    def write_scene(self, output_file, scene):
        """Write a scene to the output file"""
        try:
            json.dump(scene, output_file)
            output_file.write('\n')
            output_file.flush()
        except Exception as e:
            self.log_error(f"Error writing scene: {str(e)}")

    # ... (Other handler methods remain mostly the same, but add try/except blocks)
    # Example for one handler method:
    def handle_scene(self, line):
        """Handle scene changes (background)"""
        try:
            bg_match = re.match(r'scene\s+(.+?)(?:\s+at\s+(\S+))?$', line)
            if bg_match:
                bg_image = bg_match.group(1).strip()
                resolved_bg = self.resolve_asset(bg_image, 'image')
                self.current_bg = resolved_bg
                self.images.add(resolved_bg)
                self.current_sprites.clear()
                self.new_scene()
        except Exception as e:
            self.log_error(f"Error handling scene: {line}\n{str(e)}")

    # ... (Implement similar try/except for all handler methods)

    def write_final_output(self, output_file):
        """Write the final output structure"""
        try:
            output_file.write(json.dumps({
                'characters': self.character_defs,
                'images': sorted(self.images),
                'audio': sorted(self.audio)
            }, ensure_ascii=False))
        except Exception as e:
            self.log_error(f"Error writing final output: {str(e)}")

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
            # Write scenes as they're parsed
            parser.parse_project(f)
            
            # Write metadata at the end
            f.seek(0, os.SEEK_END)
            if f.tell() > 0:
                f.write('\n')
            parser.write_final_output(f)
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
    print(f"- Scenes: {parser.scene_count if hasattr(parser, 'scene_count') else 'N/A'}")
    print(f"- Characters: {len(parser.character_defs)}")
    print(f"- Images: {len(parser.images)}")
    print(f"- Audio files: {len(parser.audio)}")
