"""A single canonical snapshot: lock, validate, atomic publication."""
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

import yaml

from .transaction import WorkspaceLock, atomic_write

VERSION = 3


class MigrationError(ValueError):
    pass


class Loader(yaml.SafeLoader):
    # Plans and artifact metadata use JSON-compatible values; keep unquoted
    # dates as strings instead of producing nonserializable Python dates.
    yaml_implicit_resolvers = {key: [(tag, pattern) for tag, pattern in values if tag != 'tag:yaml.org,2002:timestamp'] for key, values in yaml.SafeLoader.yaml_implicit_resolvers.items()}


def unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise MigrationError(f"Invalid or duplicate YAML key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def read_yaml(path):
    try:
        data = yaml.load(Path(path).read_text(encoding='utf-8'), Loader=Loader)
    except (OSError, yaml.YAMLError) as exc:
        raise MigrationError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise MigrationError(f"Expected mapping: {path}")
    return data


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def file_hash(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return 'missing'


def local_path(project, relative):
    path = (project / relative).resolve()
    if not path.is_relative_to(project.resolve()):
        raise MigrationError(f"Path escapes project: {relative}")
    return path


class Store:
    def __init__(self, project):
        self.project = Path(project).resolve()
        self.root = self.project / '.ctrust'
        self.path = self.root / 'migration.yaml'

    def read(self):
        state = read_yaml(self.path)
        self.validate(state)
        return state

    def validate(self, state):
        if state.get('version') != VERSION:
            raise MigrationError('Unsupported migration schema; start a new migration in a new project (schema 3). No upgrade is supported.')
        for key, expected in {'revision': int, 'source': dict, 'inventory': dict, 'architecture': dict, 'progress': dict, 'preparation': dict, 'slice_results': dict, 'issues': dict, 'history': list}.items():
            if type(state.get(key)) is not expected:
                raise MigrationError(f'Invalid canonical state: {key} must be {expected.__name__}')
        if state['inventory'] and (not isinstance(state['source'].get('root'), str) or not Path(state['source']['root']).is_absolute()):
            raise MigrationError('Invalid canonical state: source.root must be an absolute path')
        inventory = state['inventory']
        if inventory and inventory.get('fingerprint') != digest({key: value for key, value in inventory.items() if key != 'fingerprint'}):
            raise MigrationError('Canonical inventory fingerprint mismatch; operational snapshots must not be edited manually')
        if inventory and inventory.get('source_root') != state['source']['root']:
            raise MigrationError('Canonical source and inventory refer to different projects')
        architecture = state['architecture']
        if architecture and (not isinstance(architecture.get('plan'), dict) or architecture.get('fingerprint') != digest(architecture['plan'])):
            raise MigrationError('Canonical architecture fingerprint mismatch; change an exported plan and confirm it through the CLI')
        if architecture and architecture['plan'].get('inventory') != inventory.get('fingerprint'):
            raise MigrationError('Architecture does not reference the canonical inventory revision')

    def publish(self, state):
        self.validate(state)
        atomic_write(self.root, 'migration.yaml', yaml.safe_dump(state, sort_keys=False, allow_unicode=True).encode())

    @contextmanager
    def change(self):
        with WorkspaceLock(self.root):
            state = self.read()
            before = digest(state)
            yield state
            if digest(state) != before:
                state['revision'] += 1
                self.publish(state)

    def initialize(self):
        self.project.mkdir(parents=True, exist_ok=True)
        with WorkspaceLock(self.root):
            if self.path.exists():
                return self.read()
            state = {'version': VERSION, 'revision': 0, 'source': {}, 'inventory': {}, 'architecture': {}, 'progress': {}, 'preparation': {}, 'slice_results': {}, 'closure': None, 'issues': {}, 'history': []}
            self.publish(state)
            return state
