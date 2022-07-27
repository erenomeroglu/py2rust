import os
from pathlib import PosixPath
from pytype import analyze, errors, config, load_pytd
from pytype.pytd import pytd_utils
from pytype.tools.merge_pyi import merge_pyi
import hashlib

# Args class taken from pytd_utils
class Args:

  def __init__(self, as_comments=False):
    self.as_comments = as_comments

  @property
  def expected_ext(self):
    """Extension of expected filename."""
    exts = {
        0: 'pep484',
        1: 'comment',
    }
    return exts[int(self.as_comments)] + '.py'

log_errors = errors.ErrorLog()

def pytype_annotate_and_merge(src: str, basedir: PosixPath, filename: PosixPath):
    pyi_dir = f"{os.getcwd()}{os.sep}{basedir}_pyi" \
        if os.path.isdir(f"{os.getcwd()}{os.sep}{basedir}") \
        else f"{os.getcwd()}{os.sep}{basedir.parent}_pyi"
    pyi_file = f"{pyi_dir}{os.sep}{filename.stem}.pyi"
    full_path = f"{os.getcwd()}{os.sep}{filename}"
    log_data: dict[str, str] = _read_log_file_contents(pyi_dir)  
    hash = _hashcontents(src)
    if os.path.isdir(pyi_dir) and \
            os.path.isfile(pyi_file) and \
            full_path in log_data and \
            hash == log_data[full_path]:
        # If a file already exists and is up-to-date, 
        # use that instead of infering the types again
        print("Types already up-to-date")
        with open(pyi_file, "r") as f:
            pyi_src = f.read()
    else:
        # Otherwise, infer the types and create the .pyi file
        print("Infering Types")
        pyi_src = _infer_types(src)
        _write_to_pyi_file(pyi_dir, pyi_file, pyi_src)
        log_data[full_path] = hash
        _write_log_file_contents(pyi_dir, log_data)
    # Create .gitignore to ignore .pyi data
    _create_gitignore(pyi_dir)
    # Set as_comments to 0
    args = Args(as_comments = 0)
    annotated_src = merge_pyi.annotate_string(args, src, pyi_src)
    return annotated_src

def _infer_types(src):
    options = config.Options.create()
    # typed_ast is an instance of TypeDeclUnit
    typed_ast, _ = analyze.infer_types(src, log_errors, 
        options, load_pytd.Loader(options))
    return pytd_utils.Print(typed_ast)

def _write_to_pyi_file(pyi_dir: str, pyi_file: str, pyi_src: str):
    if not os.path.isdir(pyi_dir):
        os.mkdir(pyi_dir)
    with open(pyi_file, "w") as f:
        f.write(pyi_src)

def _hashcontents(contents: str):
    hash_object = hashlib.sha256(bytes(contents, 'utf-8'))
    return hash_object.hexdigest()

def _read_log_file_contents(pyi_dir):
    """Reads stored contents containing 
    data about inferred python modules"""
    pyi_log_file = f"{pyi_dir}{os.sep}.log"
    log_data = {}
    if os.path.exists(pyi_log_file):
        with open(pyi_log_file, "r") as log:
            line = log.readline()
            try:
                mod_name, hash = line.split("-")
                log_data[mod_name] = hash
            except:
                raise Exception("Wrong format for pytype log data")
    return log_data

def _write_log_file_contents(pyi_dir, log_data: dict[str, str]):
    """Writes data of inferred python modules"""
    pyi_log_file = f"{pyi_dir}{os.sep}.log"
    with open(pyi_log_file, "w") as log:
        for mod_name, hash in log_data.items():
            log.write(f"{mod_name}-{hash}")

def _create_gitignore(pyi_dir):
    """Create a .gitignore similarly to how pytype does it"""
    pyi_gitignore = f"{pyi_dir}{os.sep}.gitignore"
    if not os.path.exists(pyi_gitignore):
        with open(pyi_gitignore, "w") as gitignore:
            gitignore.write("# Automatically generated by Py2Many\n")
            gitignore.write("*")