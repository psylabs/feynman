// web/grading.js — offline grading. Mirrors server/grader.py EXACTLY so an
// answer graded on-device while offline matches what the server records when
// the attempt is later synced. Keep in lockstep with grader.py.
(function () {
  "use strict";

  window.gradeAnswer = function (parsed, expected, tolerance) {
    if (parsed === null || parsed === undefined || Number.isNaN(parsed)) {
      return { correct: false, error_magnitude: null, rule: "no_answer" };
    }

    var err = parsed - expected;
    var absErr = Math.abs(err);
    var t = (tolerance && tolerance.type) || "exact";

    if (t === "exact") {
      return { correct: parsed === expected, error_magnitude: err, rule: "exact" };
    }
    if (t === "absolute") {
      var bound = parseFloat(tolerance.value);
      return { correct: absErr <= bound, error_magnitude: err, rule: "abs<=" + bound };
    }
    if (t === "relative") {
      var frac = parseFloat(tolerance.fraction);
      var rbound = Math.max(parseFloat(tolerance.min_abs || 0), Math.abs(expected) * frac);
      return { correct: absErr <= rbound, error_magnitude: err, rule: "rel<=" + frac };
    }
    if (t === "money") {
      var mbound = Math.max(1.0, Math.abs(expected) * 0.01);
      return { correct: absErr <= mbound, error_magnitude: err, rule: "money(<=$" + mbound.toFixed(2) + ")" };
    }

    return { correct: parsed === expected, error_magnitude: err, rule: "default_exact" };
  };
})();
