import { describe, expect, it } from 'vitest';
import { parseOsc7, tabLabel, terminalTitle, tildePath } from './terminalTitle';

const identity = {
  cwd: '/home/op/ros2_ws',
  shell: 'bash',
  user: 'op',
  host: 'gcs',
  home: '/home/op',
};

describe('parseOsc7', () => {
  it('reads the directory from the file URI the rcfile emits', () => {
    expect(parseOsc7('file://gcs/home/op/ros2_ws')).toBe('/home/op/ros2_ws');
    expect(parseOsc7('file:///tmp')).toBe('/tmp');
  });

  it('percent-decodes spaces and UTF-8', () => {
    expect(parseOsc7('file://gcs/tmp/dir%20com%20espa%C3%A7o')).toBe('/tmp/dir com espaço');
  });

  it('keeps a path whose escapes do not decode', () => {
    expect(parseOsc7('file://gcs/tmp/100%')).toBe('/tmp/100%');
  });

  it('refuses anything that is not a file URI', () => {
    expect(parseOsc7('http://gcs/tmp')).toBeNull();
    expect(parseOsc7('')).toBeNull();
  });
});

describe('tildePath', () => {
  it('folds the home directory, and only a whole component of it', () => {
    expect(tildePath('/home/op', '/home/op')).toBe('~');
    expect(tildePath('/home/op/ros2_ws', '/home/op/')).toBe('~/ros2_ws');
    expect(tildePath('/home/operator', '/home/op')).toBe('/home/operator');
    expect(tildePath('/etc', '/')).toBe('/etc');
  });
});

describe('terminalTitle', () => {
  it('reads user@host: path — shell', () => {
    expect(terminalTitle(identity)).toBe('op@gcs: ~/ros2_ws — bash');
    expect(terminalTitle({ ...identity, cwd: '/opt/ros/jazzy' })).toBe('op@gcs: /opt/ros/jazzy — bash');
  });
});

describe('tabLabel', () => {
  it('names a tab by the last component of its directory', () => {
    expect(tabLabel(identity)).toBe('ros2_ws');
    expect(tabLabel({ ...identity, cwd: '/home/op' })).toBe('~');
    expect(tabLabel({ ...identity, cwd: '/' })).toBe('/');
  });
});
