/**
 * `mission_config.json` as the station reads and writes it.
 *
 * - `readParameterFile` never turns a corrupt document into defaults: the
 *   operator would be shown, and would launch, figures they never set. A
 *   missing file is distinct from a corrupt one, since only the first is a
 *   fresh station.
 * - `writeJsonAtomic` gives every write its own temporary in the same
 *   directory, fsyncs it and renames it into place, then fsyncs the
 *   directory. A shared `.tmp` name let two saves in flight interleave, and a
 *   rename without fsync could leave an empty file after a power cut.
 */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

/**
 * @param {string} file
 * @returns {{success: true, params: object} | {success: false, missing: true} | {success: false, error: string}}
 */
function readParameterFile(file) {
  let text;
  try {
    text = fs.readFileSync(file, 'utf-8');
  } catch (error) {
    if (error && error.code === 'ENOENT') return { success: false, missing: true };
    return { success: false, error: `${path.basename(file)} ilegível: ${error.message}` };
  }
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    return { success: false, error: `${path.basename(file)} corrompido: ${error.message}` };
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return { success: false, error: `${path.basename(file)} não é um documento de parâmetros` };
  }
  return { success: true, params: parsed };
}

/**
 * Write `doc` to `file` as indented JSON, atomically and durably.
 *
 * @returns {string} The temporary path used, for tests.
 * @throws When serialization or any filesystem step fails; `file` is then
 *   unchanged and the temporary removed.
 */
function writeJsonAtomic(file, doc) {
  const directory = path.dirname(file);
  const temporary = path.join(
    directory,
    `.${path.basename(file)}.${process.pid}.${crypto.randomBytes(6).toString('hex')}.tmp`
  );
  let fd = null;
  try {
    const text = JSON.stringify(doc, null, 2);
    fd = fs.openSync(temporary, 'wx', 0o644);
    fs.writeSync(fd, text, 0, 'utf-8');
    fs.fsyncSync(fd);
    fs.closeSync(fd);
    fd = null;
    fs.renameSync(temporary, file);
  } catch (error) {
    if (fd !== null) fs.closeSync(fd);
    fs.rmSync(temporary, { force: true });
    throw error;
  }
  try {
    const dirFd = fs.openSync(directory, 'r');
    try {
      fs.fsyncSync(dirFd);
    } finally {
      fs.closeSync(dirFd);
    }
  } catch (_error) {
    // Directory fsync is unsupported on some filesystems; the rename stands.
  }
  return temporary;
}

module.exports = { readParameterFile, writeJsonAtomic };
