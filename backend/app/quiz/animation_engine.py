"""Self-contained JS animation engine for teaching cartoons.

Gemini outputs a JSON scene script; this engine renders it on Canvas.
The engine handles all drawing, easing, transitions — so animation
quality is consistent regardless of Gemini output.
"""
from __future__ import annotations

import json

# ── The engine JS — hand-crafted for reliable, smooth animations ──

ENGINE_JS = r"""
(function() {
  const canvas = document.getElementById('anim-canvas');
  const ctx = canvas.getContext('2d');
  const container = canvas.parentElement;
  let W, H, dpr;
  let scenes = [], currentScene = 0, sceneStart = 0, playing = true;
  let animId = null;

  /* ── Resize ── */
  function resize() {
    dpr = window.devicePixelRatio || 1;
    W = container.clientWidth;
    H = Math.round(W * 9 / 16);
    canvas.width = W * dpr; canvas.height = H * dpr;
    canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  window.addEventListener('resize', resize);
  resize();

  /* ── Easing library ── */
  const ease = {
    linear: t => t,
    easeIn: t => t * t * t,
    easeOut: t => 1 - Math.pow(1 - t, 3),
    easeInOut: t => t < .5 ? 4*t*t*t : 1 - Math.pow(-2*t+2, 3)/2,
    bounce: t => {
      const n1=7.5625, d1=2.75;
      if(t<1/d1) return n1*t*t;
      if(t<2/d1) return n1*(t-=1.5/d1)*t+.75;
      if(t<2.5/d1) return n1*(t-=2.25/d1)*t+.9375;
      return n1*(t-=2.625/d1)*t+.984375;
    },
    elastic: t => t===0?0:t===1?1:Math.pow(2,-10*t)*Math.sin((t*10-0.75)*(2*Math.PI)/3)+1,
    back: t => { const c=1.70158; return (1+c)*t*t*t - c*t*t; }
  };

  /* ── Color helpers ── */
  function hexToRgb(hex) {
    hex = hex.replace('#','');
    if(hex.length===3) hex = hex[0]+hex[0]+hex[1]+hex[1]+hex[2]+hex[2];
    const n = parseInt(hex, 16);
    return [(n>>16)&255, (n>>8)&255, n&255];
  }

  /* ── Drawing primitives ── */
  const draw = {
    circle(x, y, r, fill, stroke, lineWidth) {
      ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI*2);
      if(fill) { ctx.fillStyle = fill; ctx.fill(); }
      if(stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = lineWidth||2; ctx.stroke(); }
    },

    pieChart(cx, cy, r, totalSlices, highlighted, fill, hlFill) {
      const step = (Math.PI*2) / totalSlices;
      for(let i = 0; i < totalSlices; i++) {
        ctx.beginPath(); ctx.moveTo(cx, cy);
        ctx.arc(cx, cy, r, i*step - Math.PI/2, (i+1)*step - Math.PI/2);
        ctx.closePath();
        ctx.fillStyle = (highlighted && highlighted.includes(i)) ? (hlFill||'#FFD93D') : (fill||'#E8E8E8');
        ctx.fill();
        ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.stroke();
      }
    },

    rect(x, y, w, h, fill, radius) {
      ctx.beginPath();
      if(radius) {
        ctx.moveTo(x+radius, y);
        ctx.arcTo(x+w, y, x+w, y+h, radius);
        ctx.arcTo(x+w, y+h, x, y+h, radius);
        ctx.arcTo(x, y+h, x, y, radius);
        ctx.arcTo(x, y, x+w, y, radius);
        ctx.closePath();
      } else {
        ctx.rect(x, y, w, h);
      }
      ctx.fillStyle = fill||'#ccc'; ctx.fill();
    },

    fractionBar(x, y, w, h, total, highlighted, fill, hlFill) {
      const sw = w / total;
      for(let i = 0; i < total; i++) {
        const isFilled = highlighted && highlighted.includes(i);
        draw.rect(x + i*sw, y, sw-2, h, isFilled ? (hlFill||'#FFD93D') : (fill||'#E8E8E8'), 4);
        ctx.strokeStyle = '#fff'; ctx.lineWidth = 1;
        ctx.strokeRect(x + i*sw, y, sw-2, h);
      }
    },

    text(x, y, str, size, color, align, bold, maxW) {
      ctx.fillStyle = color || '#333';
      ctx.font = (bold?'bold ':'')+size+'px -apple-system, Arial, sans-serif';
      ctx.textAlign = align || 'center'; ctx.textBaseline = 'middle';
      if(maxW) ctx.fillText(str, x, y, maxW);
      else ctx.fillText(str, x, y);
    },

    speechBubble(x, y, w, h, str, fill, textColor, tailDir) {
      const r = 12;
      ctx.fillStyle = fill || '#fff';
      ctx.shadowColor = 'rgba(0,0,0,0.1)'; ctx.shadowBlur = 8;
      ctx.beginPath();
      ctx.moveTo(x-w/2+r, y-h/2);
      ctx.arcTo(x+w/2, y-h/2, x+w/2, y+h/2, r);
      ctx.arcTo(x+w/2, y+h/2, x-w/2, y+h/2, r);
      /* tail */
      if(tailDir !== 'none') {
        const tx = tailDir==='left' ? x-w/4 : tailDir==='right' ? x+w/4 : x;
        ctx.lineTo(tx+10, y+h/2);
        ctx.lineTo(tx, y+h/2+14);
        ctx.lineTo(tx-10, y+h/2);
      }
      ctx.arcTo(x-w/2, y+h/2, x-w/2, y-h/2, r);
      ctx.arcTo(x-w/2, y-h/2, x+w/2, y-h/2, r);
      ctx.closePath(); ctx.fill();
      ctx.shadowBlur = 0;
      /* text */
      ctx.fillStyle = textColor || '#333';
      ctx.font = 'bold '+Math.max(12, Math.min(16, w/15))+'px -apple-system, Arial, sans-serif';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      /* word wrap */
      const words = str.split(' '); let line = '', ly = y - h/4;
      const maxLW = w - 20, lh = 20;
      const lines = [];
      for(const word of words) {
        const test = line + word + ' ';
        if(ctx.measureText(test).width > maxLW && line) { lines.push(line.trim()); line = word + ' '; }
        else line = test;
      }
      lines.push(line.trim());
      const startY = y - (lines.length-1)*lh/2;
      lines.forEach((l,i) => ctx.fillText(l, x, startY + i*lh));
    },

    arrow(x1, y1, x2, y2, color, width) {
      const angle = Math.atan2(y2-y1, x2-x1);
      const len = 10;
      ctx.strokeStyle = color||'#666'; ctx.lineWidth = width||3;
      ctx.lineCap = 'round';
      ctx.beginPath(); ctx.moveTo(x1,y1); ctx.lineTo(x2,y2); ctx.stroke();
      ctx.fillStyle = color||'#666'; ctx.beginPath();
      ctx.moveTo(x2, y2);
      ctx.lineTo(x2-len*Math.cos(angle-0.4), y2-len*Math.sin(angle-0.4));
      ctx.lineTo(x2-len*Math.cos(angle+0.4), y2-len*Math.sin(angle+0.4));
      ctx.closePath(); ctx.fill();
    },

    star(cx, cy, r, fill) {
      ctx.fillStyle = fill||'#FFD93D';
      ctx.beginPath();
      for(let i=0;i<5;i++){
        const a = (i*4*Math.PI/5) - Math.PI/2;
        const m = i===0?'moveTo':'lineTo';
        ctx[m](cx+r*Math.cos(a), cy+r*Math.sin(a));
        const b = a + 2*Math.PI/5;
        ctx.lineTo(cx+r*0.4*Math.cos(b), cy+r*0.4*Math.sin(b));
      }
      ctx.closePath(); ctx.fill();
    },

    confetti(particles) {
      particles.forEach(p => {
        ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.rot);
        ctx.fillStyle = p.color;
        ctx.fillRect(-p.w/2, -p.h/2, p.w, p.h);
        ctx.restore();
      });
    },

    numberLine(x, y, w, min, max, marks, highlight, color) {
      ctx.strokeStyle = color||'#333'; ctx.lineWidth = 3; ctx.lineCap = 'round';
      ctx.beginPath(); ctx.moveTo(x,y); ctx.lineTo(x+w,y); ctx.stroke();
      const step = w / (max - min);
      for(let v = min; v <= max; v++) {
        const px = x + (v-min)*step;
        ctx.beginPath(); ctx.moveTo(px, y-6); ctx.lineTo(px, y+6); ctx.stroke();
        draw.text(px, y+18, ''+v, 12, color||'#333', 'center');
      }
      if(marks) marks.forEach(m => {
        const px = x + (m.value-min)*step;
        draw.circle(px, y, 6, m.color||highlight||'#FF6B6B');
        if(m.label) draw.text(px, y-16, m.label, 11, m.color||'#FF6B6B', 'center', true);
      });
    }
  };

  /* ── Effect helpers ── */
  function getEnterTransform(effect, progress, el) {
    const p = Math.max(0, Math.min(1, progress));
    const e = ease[el.easing || 'easeOut'](p);
    switch(effect) {
      case 'slideLeft':  return { ox: (1-e)*(-W*0.4), oy: 0, scale: 1, alpha: e };
      case 'slideRight': return { ox: (1-e)*(W*0.4), oy: 0, scale: 1, alpha: e };
      case 'slideUp':    return { ox: 0, oy: (1-e)*(H*0.3), scale: 1, alpha: e };
      case 'slideDown':  return { ox: 0, oy: (1-e)*(-H*0.3), scale: 1, alpha: e };
      case 'fadeIn':     return { ox: 0, oy: 0, scale: 1, alpha: e };
      case 'scaleIn':    return { ox: 0, oy: 0, scale: e, alpha: e };
      case 'bounceIn':   return { ox: 0, oy: 0, scale: ease.elastic(p), alpha: Math.min(1,p*3) };
      case 'pop':        return { ox: 0, oy: 0, scale: ease.back(p), alpha: Math.min(1,p*2) };
      default:           return { ox: 0, oy: 0, scale: 1, alpha: 1 };
    }
  }

  /* ── Confetti system ── */
  const confettiColors = ['#FF6B6B','#4ECDC4','#45B7D1','#96E6A1','#FFD93D','#FF8E53','#DDA0DD'];
  let confettiParticles = [];
  function spawnConfetti(cx, cy, count) {
    for(let i=0;i<(count||40);i++) {
      confettiParticles.push({
        x: cx, y: cy,
        vx: (Math.random()-0.5)*8, vy: -Math.random()*6-2,
        w: Math.random()*8+4, h: Math.random()*4+2,
        rot: Math.random()*Math.PI*2, vr: (Math.random()-0.5)*0.3,
        color: confettiColors[Math.floor(Math.random()*confettiColors.length)],
        life: 1
      });
    }
  }
  function updateConfetti(dt) {
    confettiParticles = confettiParticles.filter(p => {
      p.x += p.vx; p.y += p.vy; p.vy += 0.15;
      p.rot += p.vr; p.life -= dt*0.5;
      return p.life > 0;
    });
    if(confettiParticles.length) draw.confetti(confettiParticles);
  }

  /* ── Scene renderer ── */
  function renderScene(scene, progress, dt) {
    /* background */
    if(scene.background) {
      if(scene.background.includes(',')) {
        const grd = ctx.createLinearGradient(0,0,0,H);
        const cols = scene.background.split(',');
        cols.forEach((c,i)=> grd.addColorStop(i/(cols.length-1), c.trim()));
        ctx.fillStyle = grd;
      } else {
        ctx.fillStyle = scene.background;
      }
      ctx.fillRect(0,0,W,H);
    } else {
      ctx.fillStyle = '#FFF8E1'; ctx.fillRect(0,0,W,H);
    }

    /* progress bar */
    const totalDur = scenes.reduce((s,sc)=>s+sc.duration,0);
    let elapsed = 0;
    for(let i=0;i<currentScene;i++) elapsed += scenes[i].duration;
    elapsed += progress * scene.duration;
    const totalProg = elapsed / totalDur;
    draw.rect(0, H-4, W * totalProg, 4, '#6366f1');

    /* elements */
    (scene.elements || []).forEach(el => {
      const delay = el.delay || 0;
      const dur = el.enterDuration || 0.8;
      const elProgress = Math.max(0, (progress * scene.duration - delay) / dur);

      if(elProgress <= 0) return;

      const tr = getEnterTransform(el.enter || 'fadeIn', Math.min(1, elProgress), el);
      ctx.save();
      ctx.globalAlpha = tr.alpha;

      /* resolve positions as fractions of W/H */
      const ex = (el.x||0) * W + tr.ox;
      const ey = (el.y||0) * H + tr.oy;

      if(tr.scale !== 1) {
        ctx.translate(ex, ey);
        ctx.scale(tr.scale, tr.scale);
        ctx.translate(-ex, -ey);
      }

      /* wobble effect */
      if(el.wobble && elProgress >= 1) {
        const wobT = (progress * scene.duration - delay - dur) * 4;
        const wobble = Math.sin(wobT) * 2;
        ctx.translate(wobble, 0);
      }

      switch(el.type) {
        case 'circle':
          draw.circle(ex, ey, (el.r||0.08)*W, el.fill, el.stroke, el.lineWidth);
          if(el.label) draw.text(ex, ey, el.label, (el.fontSize||0.04)*W, el.labelColor||'#333', 'center', true);
          break;
        case 'pie':
          draw.pieChart(ex, ey, (el.r||0.1)*W, el.slices||4, el.highlight, el.fill, el.hlFill);
          if(el.label) draw.text(ex, ey + (el.r||0.1)*W + 20, el.label, (el.fontSize||0.035)*W, el.labelColor||'#333', 'center', true);
          break;
        case 'fractionBar':
          draw.fractionBar(ex - (el.w||0.3)*W/2, ey - (el.h||0.06)*H/2,
            (el.w||0.3)*W, (el.h||0.06)*H, el.parts||4, el.highlight, el.fill, el.hlFill);
          if(el.label) draw.text(ex, ey + (el.h||0.06)*H/2 + 16, el.label, (el.fontSize||0.035)*W, el.labelColor||'#333', 'center', true);
          break;
        case 'text':
          draw.text(ex, ey, el.text||'', (el.size||0.05)*W, el.fill||'#333', 'center', el.bold!==false, el.maxWidth?el.maxWidth*W:undefined);
          break;
        case 'speechBubble':
          draw.speechBubble(ex, ey, (el.w||0.5)*W, (el.h||0.15)*H, el.text||'', el.fill||'#fff', el.textColor, el.tail||'down');
          break;
        case 'arrow':
          draw.arrow(ex, ey, (el.x2||0)*W, (el.y2||0)*H, el.fill||'#666', el.lineWidth||3);
          break;
        case 'star':
          draw.star(ex, ey, (el.r||0.03)*W, el.fill||'#FFD93D');
          break;
        case 'rect':
          draw.rect(ex-(el.w||0.1)*W/2, ey-(el.h||0.1)*H/2, (el.w||0.1)*W, (el.h||0.1)*H, el.fill||'#ccc', el.radius||0);
          if(el.label) draw.text(ex, ey, el.label, (el.fontSize||0.03)*W, el.labelColor||'#fff', 'center', true);
          break;
        case 'numberLine':
          draw.numberLine(ex, ey, (el.w||0.7)*W, el.min||0, el.max||10, el.marks, el.markColor, el.fill);
          break;
        case 'confetti':
          if(elProgress > 0 && elProgress < 0.1) spawnConfetti(ex, ey, el.count||50);
          break;
      }
      ctx.restore();
    });

    updateConfetti(dt);
  }

  /* ── Main loop ── */
  let lastTime = 0;
  function tick(ts) {
    if(!lastTime) lastTime = ts;
    const dt = (ts - lastTime) / 1000;
    lastTime = ts;

    if(!playing || currentScene >= scenes.length) {
      if(currentScene >= scenes.length) showReplay();
      return;
    }

    const scene = scenes[currentScene];
    const elapsed = (ts - sceneStart) / 1000;
    const progress = Math.min(1, elapsed / scene.duration);

    renderScene(scene, progress, dt);

    if(progress >= 1) {
      currentScene++;
      sceneStart = ts;
      if(currentScene >= scenes.length) {
        renderScene(scene, 1, dt); /* final frame */
        showReplay();
        return;
      }
    }
    animId = requestAnimationFrame(tick);
  }

  /* ── Replay ── */
  function showReplay() {
    const btn = document.getElementById('anim-replay');
    if(btn) btn.style.display = 'inline-block';
  }

  window.__animReplay = function() {
    const btn = document.getElementById('anim-replay');
    if(btn) btn.style.display = 'none';
    currentScene = 0; sceneStart = 0; lastTime = 0;
    confettiParticles = [];
    playing = true;
    animId = requestAnimationFrame(tick);
  };

  /* ── Init ── */
  window.__animInit = function(sceneData) {
    scenes = sceneData;
    currentScene = 0; sceneStart = 0; lastTime = 0; playing = true;
    confettiParticles = [];
    resize();
    animId = requestAnimationFrame(tick);
  };
})();
"""


def build_animation_html(scene_json: list[dict]) -> str:
    """Combine the engine JS + Gemini scene data into a self-contained HTML snippet."""
    scene_data = json.dumps(scene_json, ensure_ascii=False)

    return f"""<div id="animation-root" style="max-width:640px;margin:0 auto;">
<canvas id="anim-canvas" style="display:block;width:100%;border-radius:12px;box-shadow:0 4px 20px rgba(0,0,0,0.12);"></canvas>
<div style="text-align:center;margin-top:10px;">
<button id="anim-replay" onclick="window.__animReplay()"
  style="display:none;padding:10px 24px;background:#6366f1;color:white;border:none;
  border-radius:10px;font-size:1em;font-weight:600;cursor:pointer;
  box-shadow:0 2px 8px rgba(99,102,241,0.3);">
  ▶ Replay
</button>
</div>
</div>
<script>
{ENGINE_JS}
window.__animInit({scene_data});
</script>"""
