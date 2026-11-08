/* Plate scanner: the officer photographs a number plate and the browser reads it.
 *
 * The text is read on the officer's own device (Tesseract compiled to WebAssembly, bundled in
 * /static/vendor/tesseract), so the photo never leaves the phone and no OCR service is needed.
 * A reading is a suggestion: the officer confirms it before anything is looked up or recorded.
 */
(function () {
  var BASE = "/static/vendor/tesseract/";
  var workerPromise = null;

  function loadScript(src) {
    return new Promise(function (ok, bad) {
      if (window.Tesseract) return ok();
      var s = document.createElement("script");
      s.src = src; s.onload = ok; s.onerror = function () { bad(new Error("Could not load the scanner")); };
      document.head.appendChild(s);
    });
  }

  function getWorker(progress) {
    if (!workerPromise) {
      workerPromise = loadScript(BASE + "tesseract.min.js").then(function () {
        return window.Tesseract.createWorker("eng", 1, {
          workerPath: BASE + "worker.min.js", corePath: BASE, langPath: BASE, workerBlobURL: false, gzip: true,
          logger: function (m) { if (progress && m.status) progress(m.status, m.progress || 0); },
        });
      }).catch(function (e) { workerPromise = null; throw e; });
    }
    return workerPromise;
  }

  // Zambian plates are three letters then three or four digits (BAK 123, ALF 4567).
  var TO_LETTER = { "0": "O", "1": "I", "5": "S", "8": "B", "2": "Z", "6": "G", "4": "A" };
  var TO_DIGIT = { O: "0", Q: "0", D: "0", I: "1", L: "1", S: "5", B: "8", Z: "2", G: "6", A: "4" };

  function shape(token) {
    // Fix look-alike characters by position: the first three are letters, the rest are digits.
    var t = token.replace(/[^A-Z0-9]/g, "");
    if (t.length < 6 || t.length > 7) return null;
    var out = "";
    for (var i = 0; i < t.length; i++) {
      var c = t[i];
      if (i < 3) out += /[0-9]/.test(c) ? (TO_LETTER[c] || "") : c;
      else out += /[A-Z]/.test(c) ? (TO_DIGIT[c] || "") : c;
    }
    return /^[A-Z]{3}[0-9]{3,4}$/.test(out) ? out.slice(0, 3) + " " + out.slice(3) : null;
  }

  // Candidate plates from OCR output: try each line, and each pair of neighbouring words.
  function candidates(data) {
    var found = {};
    function add(plate, conf) { if (plate && (!found[plate] || found[plate] < conf)) found[plate] = conf; }
    (data.lines || []).forEach(function (ln) {
      var words = (ln.words || []).filter(function (w) { return w.text && w.text.trim(); });
      var all = ln.text.toUpperCase();
      add(shape(all.replace(/[^A-Z0-9]/g, "")), ln.confidence || 0);
      for (var i = 0; i < words.length; i++) {
        var a = words[i].text.toUpperCase(), c1 = words[i].confidence || 0;
        add(shape(a), c1);
        if (words[i + 1]) add(shape(a + words[i + 1].text.toUpperCase()), Math.min(c1, words[i + 1].confidence || 0));
      }
    });
    return Object.keys(found).map(function (p) { return { plate: p, confidence: Math.round(found[p]) }; })
      .sort(function (x, y) { return y.confidence - x.confidence; });
  }

  // Grey, stretched contrast, limited size: small, clear text reads far better than a 12 MP photo.
  async function prepare(file, invert, crop) {
    var bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
    var sx = 0, sy = 0, sw = bmp.width, sh = bmp.height;
    if (crop) { sx = bmp.width * crop[0]; sy = bmp.height * crop[1]; sw = bmp.width * crop[2]; sh = bmp.height * crop[3]; }
    var scale = Math.min(crop ? 1.6 : 1, 1600 / Math.max(sw, sh));
    var w = Math.max(1, Math.round(sw * scale)), h = Math.max(1, Math.round(sh * scale));
    var cv = document.createElement("canvas"); cv.width = w; cv.height = h;
    var cx = cv.getContext("2d", { willReadFrequently: true });
    cx.imageSmoothingQuality = "high";
    cx.drawImage(bmp, sx, sy, sw, sh, 0, 0, w, h);
    var img = cx.getImageData(0, 0, w, h), d = img.data, lo = 255, hi = 0, i, g;
    for (i = 0; i < d.length; i += 4) { g = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2]; d[i] = g; if (g < lo) lo = g; if (g > hi) hi = g; }
    var span = Math.max(1, hi - lo);
    for (i = 0; i < d.length; i += 4) {
      g = (d[i] - lo) * 255 / span; if (invert) g = 255 - g;
      d[i] = d[i + 1] = d[i + 2] = g; d[i + 3] = 255;
    }
    cx.putImageData(img, 0, 0);
    return cv;
  }

  async function scan(file, progress) {
    var worker = await getWorker(progress);
    var all = {}, preview = null;
    var MID = [0.15, 0.3, 0.7, 0.45];  // the plate is usually near the middle: read that part enlarged
    var passes = [[false, "11", null], [false, "11", MID], [false, "7", MID], [true, "11", MID], [false, "6", null], [true, "11", null]];
    for (var p = 0; p < passes.length; p++) {
      var cv = await prepare(file, passes[p][0], passes[p][2]);
      if (!preview && !passes[p][2]) preview = cv.toDataURL("image/jpeg", 0.6);
      await worker.setParameters({ tessedit_pageseg_mode: passes[p][1], tessedit_char_whitelist: "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 " });
      var res = await worker.recognize(cv, {}, { blocks: true });
      var data = res.data;
      if (!data.lines && data.blocks) {  // v5.x returns blocks/paragraphs/lines
        data.lines = [];
        data.blocks.forEach(function (b) { (b.paragraphs || []).forEach(function (pg) { (pg.lines || []).forEach(function (l) { data.lines.push(l); }); }); });
      }
      candidates(data).forEach(function (c) { if (!all[c.plate] || all[c.plate] < c.confidence) all[c.plate] = c.confidence; });
      if (Object.keys(all).some(function (k) { return all[k] >= 80; })) break;  // a confident read: stop early
      if (progress) progress("trying another reading", (p + 1) / passes.length);
    }
    var list = Object.keys(all).map(function (k) { return { plate: k, confidence: all[k] }; })
      .sort(function (a, b) { return b.confidence - a.confidence; });
    var good = list.filter(function (c) { return c.confidence >= 25; });  // drop junk guesses when a real reading exists
    return { candidates: good.length ? good : list.slice(0, 2), preview: preview };
  }

  window.PlateScan = { scan: scan, shape: shape, candidates: candidates };
})();
