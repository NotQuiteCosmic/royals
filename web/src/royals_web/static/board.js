// Drawing the board, and turning a click back into a square.
//
// This file knows how a square LOOKS. It does not know a single rule: which squares can
// be clicked, which moves are legal and what a move does are all answered by the server,
// which runs the same engine the computer plays by. Everything here is presentation, so
// a bug in it can make the board ugly or confusing but cannot make an illegal move legal.
//
// Geometry: index = (rank - 1) * 7 + file, so index 0 is a1. Rank 1 is drawn at the
// BOTTOM, chess-fashion, which means row 0 on screen is rank 7.

const N = 7;

export const FILES = ["a", "b", "c", "d", "e", "f", "g"];

export function algToIndex(alg) {
  return (parseInt(alg[1], 10) - 1) * N + FILES.indexOf(alg[0]);
}

export function indexToAlg(index) {
  return FILES[index % N] + (Math.floor(index / N) + 1);
}

// screen row/col -> board index
function rcToIndex(row, col) {
  return (N - 1 - row) * N + col;
}

function indexToRC(index) {
  return { row: N - 1 - Math.floor(index / N), col: index % N };
}

const palette = () => {
  const css = getComputedStyle(document.documentElement);
  const v = (name, fallback) => (css.getPropertyValue(name).trim() || fallback);
  return {
    blue: v("--blue", "#5b8dd6"),
    red: v("--red", "#d1594f"),
    gold: v("--gold", "#d8b25c"),
    green: v("--green", "#6fae6a"),
    ink: v("--ink", "#e8e4da"),
    dim: v("--dim", "#9aa0ab"),
    line: v("--line", "#2c313a"),
  };
};

const isDark = () =>
  window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;

export class BoardView {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.state = null;
    this.marks = new Map();   // alg -> mark kind
    this.selected = null;
    this.lastMove = [];
    this.size = 700;
  }

  resize() {
    const rect = this.canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const css = Math.max(280, Math.round(rect.width));
    this.size = css;
    this.canvas.width = Math.round(css * dpr);
    this.canvas.height = Math.round(css * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  cell() { return this.size / N; }

  // -- hit testing --------------------------------------------------------

  hit(clientX, clientY) {
    const rect = this.canvas.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    if (x < 0 || y < 0 || x >= rect.width || y >= rect.height) return null;

    const cell = rect.width / N;
    const col = Math.floor(x / cell);
    const row = Math.floor(y / cell);
    if (col < 0 || col >= N || row < 0 || row >= N) return null;
    return indexToAlg(rcToIndex(row, col));
  }

  // -- drawing ------------------------------------------------------------

  draw(state, marks, selected, lastMove) {
    if (state) this.state = state;
    if (marks) this.marks = marks;
    this.selected = selected || null;
    this.lastMove = lastMove || [];

    const ctx = this.ctx;
    const c = this.cell();
    const p = palette();
    const dark = isDark();

    ctx.clearRect(0, 0, this.size, this.size);

    for (let row = 0; row < N; row++) {
      for (let col = 0; col < N; col++) {
        const index = rcToIndex(row, col);
        const alg = indexToAlg(index);
        const x = col * c, y = row * c;

        // square
        const light = (row + col) % 2 === 0;
        ctx.fillStyle = dark
          ? (light ? "#262a31" : "#1f232a")
          : (light ? "#e6e0d2" : "#d9d2c2");
        ctx.fillRect(x, y, c, c);

        if (this.lastMove.includes(alg)) {
          ctx.fillStyle = dark ? "rgba(216,178,92,.16)" : "rgba(154,116,32,.18)";
          ctx.fillRect(x, y, c, c);
        }

        this.drawMark(x, y, c, this.marks.get(alg), p);

        if (this.selected === alg) {
          ctx.strokeStyle = p.gold;
          ctx.lineWidth = 3;
          ctx.strokeRect(x + 1.5, y + 1.5, c - 3, c - 3);
        }

        const square = this.state ? this.state.board[index] : null;
        if (square) this.drawStack(x, y, c, square, p);

        this.drawCoords(x, y, c, row, col, p);
      }
    }

    // grid
    ctx.strokeStyle = dark ? "rgba(0,0,0,.5)" : "rgba(0,0,0,.16)";
    ctx.lineWidth = 1;
    for (let i = 0; i <= N; i++) {
      ctx.beginPath(); ctx.moveTo(i * c, 0); ctx.lineTo(i * c, this.size); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(0, i * c); ctx.lineTo(this.size, i * c); ctx.stroke();
    }
  }

  drawMark(x, y, c, mark, p) {
    if (!mark) return;
    const ctx = this.ctx;
    const mid = c / 2;

    const fills = {
      enter: "rgba(111,174,106,.30)",
      jump: "rgba(111,174,106,.30)",
      push: "rgba(91,141,214,.30)",
      free: "rgba(216,178,92,.30)",
      origin: "rgba(216,178,92,.13)",
      break: "rgba(209,89,79,.26)",
    };

    ctx.fillStyle = fills[mark] || fills.jump;
    ctx.fillRect(x, y, c, c);

    // A wrapped jump gets a ring, because a destination on the far side of the board is
    // the single most confusing thing about this game for someone meeting it new.
    if (mark === "wrap") {
      ctx.fillStyle = fills.jump;
      ctx.fillRect(x, y, c, c);
      ctx.strokeStyle = p.gold;
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.arc(x + mid, y + mid, c * 0.34, 0, Math.PI * 2);
      ctx.stroke();
    }

    if (mark === "break") {
      ctx.strokeStyle = p.red;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(x + mid, y + mid, c * 0.22, 0, Math.PI * 2);
      ctx.stroke();
    }
  }

  drawStack(x, y, c, s, p) {
    const ctx = this.ctx;
    const colour = s.side === 0 ? p.blue : p.red;
    const hasPris = s.prisFlag && (s.capSpy || s.capPawns);
    const bodyH = hasPris ? c * 0.60 : c * 0.74;
    const pad = c * 0.13;

    // the stack itself
    ctx.fillStyle = colour;
    roundRect(ctx, x + pad, y + pad, c - pad * 2, bodyH, c * 0.10);
    ctx.fill();

    ctx.fillStyle = isDark() ? "#12141a" : "#fbf8f0";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";

    if (s.dragon) {
      ctx.font = `600 ${label(c, 0.30, 12)}px ui-monospace, Menlo, monospace`;
      ctx.fillText("D", x + c / 2, y + pad + bodyH / 2);
    } else {
      const bits = [];
      if (s.royal) bits.push("R");
      if (s.spy) bits.push("S");
      if (s.pawns) bits.push(String(s.pawns));
      ctx.font = `600 ${label(c, 0.26, 11)}px ui-monospace, Menlo, monospace`;
      ctx.fillText(bits.join(" ") || "·", x + c / 2, y + pad + bodyH / 2);
    }

    // prisoners, in the colour of whoever they belong to -- the OTHER side
    if (hasPris) {
      const enemy = s.side === 0 ? p.red : p.blue;
      const stripY = y + pad + bodyH + c * 0.03;
      const stripH = c * 0.17;
      ctx.globalAlpha = 0.75;
      ctx.fillStyle = enemy;
      roundRect(ctx, x + pad, stripY, c - pad * 2, stripH, c * 0.05);
      ctx.fill();
      ctx.globalAlpha = 1;

      const held = [];
      if (s.capSpy) held.push("S");
      if (s.capPawns) held.push(String(s.capPawns));
      ctx.fillStyle = isDark() ? "#12141a" : "#fbf8f0";
      ctx.font = `600 ${label(c, 0.13, 9)}px ui-monospace, Menlo, monospace`;
      ctx.fillText(held.join(" "), x + c / 2, stripY + stripH / 2);
    }
  }

  drawCoords(x, y, c, row, col, p) {
    const ctx = this.ctx;
    ctx.fillStyle = p.dim;
    ctx.globalAlpha = 0.75;
    ctx.font = `${label(c, 0.13, 9)}px ui-monospace, Menlo, monospace`;
    ctx.textBaseline = "top";
    if (col === 0) {
      ctx.textAlign = "left";
      ctx.fillText(String(N - row), x + 3, y + 3);
    }
    if (row === N - 1) {
      ctx.textAlign = "right";
      ctx.fillText(FILES[col], x + c - 3, y + c - c * 0.19);
    }
    ctx.globalAlpha = 1;
  }
}

// Text sized to the cell, but never below what a person can read.
//
// Everything on a square scales with the cell so the board looks the same at any size,
// which is right until the cell gets small. On a 360px phone a cell is about 50px, and
// the prisoner strip's 0.13 of that is 6px -- present, correctly positioned, and
// illegible. The floor costs nothing on a desktop, where the proportional size is
// always the larger of the two.
function label(cell, ratio, floor) {
  return Math.max(floor, Math.round(cell * ratio));
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

// Presentational only. A jump is diagonal, so on an unwrapped one the file and rank
// deltas match; when it leaves one edge and reappears at the other they no longer do.
// Getting this wrong mislabels a highlight -- it cannot affect what is legal, because
// the server decides that.
export function looksWrapped(fromAlg, toAlg) {
  const df = Math.abs(FILES.indexOf(fromAlg[0]) - FILES.indexOf(toAlg[0]));
  const dr = Math.abs(parseInt(fromAlg[1], 10) - parseInt(toAlg[1], 10));
  return df !== dr;
}
