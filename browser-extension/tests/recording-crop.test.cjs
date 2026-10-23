"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), vm = require("node:vm"), fs = require("node:fs"), path = require("node:path");
const context = vm.createContext({setTimeout, clearTimeout});
vm.runInContext(fs.readFileSync(path.join(__dirname, "../recording-crop.js"), "utf8"), context);
const {regionPixels, createCrop} = context.OpenDownloaderCrop;
test("crop coordinates bind to source pixels, independent of preview scale", () => {
  assert.deepEqual(JSON.parse(JSON.stringify(regionPixels({x: .25, y: .25, width: .5, height: .5}, 1920, 1080))), {x: 480, y: 270, width: 960, height: 540});
  assert.deepEqual(JSON.parse(JSON.stringify(regionPixels({x: 0, y: 0, width: 1, height: 1}, 101, 99))), {x: 0, y: 0, width: 100, height: 98});
});
test("invalid, outside, empty and excessive crop areas fail before allocation", () => {
  for (const region of [null, {x: NaN, y: 0, width: 1, height: 1}, {x: -.1, y: 0, width: .5, height: 1}, {x: .5, y: 0, width: 1, height: 1}, {x: 0, y: 0, width: 0, height: 1}]) assert.throws(() => regionPixels(region, 1920, 1080));
  assert.throws(() => regionPixels({x: 0, y: 0, width: 1, height: 1}, 7680, 4320), /4096/);
});
test("crop owns derived video and callbacks; consented audio remains with source", () => {
  let callback, stopped = 0, cancelled = 0; const draw = [], audio = {kind: "audio", stop() {throw new Error("crop must not own audio");}};
  const videoTrack = {stop() {stopped++;}}, context = {drawImage(...args) {draw.push(args);}};
  const canvas = {getContext() {return context;}, captureStream() {return {getVideoTracks: () => [videoTrack]};}};
  const video = {videoWidth: 1920, videoHeight: 1080};
  const timing = {setTimeout(fn) {callback = fn; return 7;}, clearTimeout(id) {assert.equal(id, 7); cancelled++;}};
  class Stream {constructor(tracks) {this.tracks = tracks;}}
  const output = createCrop(video, {getAudioTracks: () => [audio]}, {x: .25, y: .25, width: .5, height: .5}, {document: {createElement: () => canvas}, Stream, timing});
  assert.equal(output.width, 960); assert.equal(output.stream.tracks[0],videoTrack); assert.equal(output.stream.tracks[1],audio);
  assert.deepEqual(draw[0].slice(1), [480, 270, 960, 540, 0, 0, 960, 540]);
  callback(); assert.equal(draw.length, 2); output.release(); output.release(); callback();
  assert.equal(draw.length, 2); assert.equal(stopped, 1); assert.equal(cancelled, 1);
});
test("source resize interrupts crop instead of recording stale area", () => {
  let callback, failure, stopped = 0;
  const video = {videoWidth: 640, videoHeight: 480};
  const timing = {setTimeout(fn) {callback = fn;}, clearTimeout() {}};
  const canvas = {getContext: () => ({drawImage() {}}), captureStream: () => ({getVideoTracks: () => [{stop() {stopped++;}}]})};
  createCrop(video, {getAudioTracks: () => []}, {x: .5, y: .5, width: .5, height: .5}, {document: {createElement: () => canvas}, Stream: class {}, timing, onError: error => {failure = error;}});
  video.videoWidth = 200; callback(); assert.match(failure.message, /resolução/); assert.equal(stopped, 1);
});
