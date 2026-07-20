import { execFileSync } from 'node:child_process';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const sampleRate = 16_000;
const durationSeconds = 378;
const sampleCount = sampleRate * durationSeconds;
const root = dirname(dirname(fileURLToPath(import.meta.url)));
const output = resolve(root, 'public/audio/echoledger-el1042.wav');

// These exact utterances are pre-authored fixtures, not generated analysis. The
// macOS voices are used only by this offline asset-authoring script; the checked
// in WAV has no runtime dependency on speech services, tools, or network access.
const segments = [
  { start: 0, end: 32, voice: 'Reed', rate: 156, text: 'Thanks for calling Northstar support. I have opened a fictional diagnostic workspace for this review.' },
  { start: 32, end: 76, voice: 'Samantha', rate: 150, text: 'Our operations group is preparing the quarterly workspace archive.' },
  { start: 76, end: 136, voice: 'Reed', rate: 156, text: 'I will walk through the bundled export checklist with you.' },
  { start: 136, end: 151, voice: 'Samantha', rate: 150, text: 'I restarted the workspace twice before calling.' },
  { start: 151, end: 168, voice: 'Samantha', rate: 145, text: 'Every time our team exports the quarterly workspace, the progress reaches ninety-eight percent and then stalls.' },
  { start: 168, end: 202, voice: 'Samantha', rate: 150, text: 'You can reach me at mara dot chen at example dot test after the review.' },
  { start: 202, end: 252, voice: 'Reed', rate: 156, text: 'The fictional diagnostic log shows the export reaching its final packaging step.' },
  { start: 252, end: 281, voice: 'Reed', rate: 152, text: 'I will send the export diagnostics checklist by four this afternoon.' },
  { start: 281, end: 333, voice: 'Samantha', rate: 150, text: 'Please include the exact final-step checks so our team can compare the next attempt.' },
  { start: 333, end: 378, voice: 'Reed', rate: 156, text: 'I have captured that request in this local fictional case.' },
];

function dataChunk(wav) {
  const offset = wav.indexOf(Buffer.from('data'));
  if (offset < 0) throw new Error('Converted spoken segment has no WAV data chunk');
  return wav.subarray(offset + 8, offset + 8 + wav.readUInt32LE(offset + 4));
}

function makeWav(samples) {
  const dataBytes = samples.byteLength;
  const wav = Buffer.alloc(44 + dataBytes);
  wav.write('RIFF', 0); wav.writeUInt32LE(36 + dataBytes, 4); wav.write('WAVE', 8);
  wav.write('fmt ', 12); wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(sampleRate, 24); wav.writeUInt32LE(sampleRate * 2, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34);
  wav.write('data', 36); wav.writeUInt32LE(dataBytes, 40); Buffer.from(samples.buffer).copy(wav, 44);
  return wav;
}

const temporaryDirectory = await mkdtemp(join(tmpdir(), 'echoledger-spoken-call-'));
try {
  const samples = new Int16Array(sampleCount);
  for (const [index, segment] of segments.entries()) {
    const aiff = join(temporaryDirectory, `${index}.aiff`);
    const wav = join(temporaryDirectory, `${index}.wav`);
    execFileSync('/usr/bin/say', ['-v', segment.voice, '-r', String(segment.rate), '-o', aiff, segment.text], { stdio: 'inherit' });
    execFileSync('/opt/homebrew/bin/ffmpeg', ['-loglevel', 'error', '-y', '-i', aiff, '-ac', '1', '-ar', String(sampleRate), '-c:a', 'pcm_s16le', wav], { stdio: 'inherit' });
    const speech = dataChunk(await readFile(wav));
    if (!speech.byteLength) throw new Error(`Offline speech synthesis produced no audio for fixture ${index}`);
    const speechSamples = new Int16Array(speech.buffer, speech.byteOffset, Math.floor(speech.byteLength / 2));
    const destination = Math.floor((segment.start + 0.35) * sampleRate);
    const maximum = Math.floor((segment.end - segment.start - 0.7) * sampleRate);
    if (speechSamples.length > maximum) throw new Error(`Spoken fixture ${index} exceeds its canonical segment boundary`);
    samples.set(speechSamples, destination);
  }
  await mkdir(dirname(output), { recursive: true });
  await writeFile(output, makeWav(samples));
  console.log(`Generated ${durationSeconds}s intelligible local fictional spoken call at ${output}`);
} finally {
  await rm(temporaryDirectory, { recursive: true, force: true });
}
