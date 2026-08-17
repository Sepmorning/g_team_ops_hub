(() => {
  'use strict';

  const canvas = document.getElementById('petalCanvas');
  if (!canvas) return;

  const context = canvas.getContext('2d', { alpha: true });
  if (!context) return;

  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const motionKey = 'g_team_ops_motion';
  let width = 0;
  let height = 0;
  let ratio = 1;
  let petals = [];
  let frameId = 0;
  let lastTime = 0;

  const random = (min, max) => min + Math.random() * (max - min);
  const canAnimate = () => {
    let stored = null;
    try { stored = localStorage.getItem(motionKey); } catch (_) { /* 无本地存储时沿用默认值 */ }
    return stored !== 'off' && !reducedMotion.matches && !document.hidden;
  };

  function makeSprite(fill, highlight) {
    const sprite = document.createElement('canvas');
    sprite.width = 64;
    sprite.height = 64;
    const brush = sprite.getContext('2d');
    const gradient = brush.createLinearGradient(18, 14, 46, 50);
    gradient.addColorStop(0, highlight);
    gradient.addColorStop(1, fill);
    brush.translate(32, 32);
    brush.rotate(-.18);
    brush.beginPath();
    brush.moveTo(0, 25);
    brush.bezierCurveTo(-25, 13, -22, -18, 0, -27);
    brush.bezierCurveTo(23, -15, 24, 13, 0, 25);
    brush.closePath();
    brush.fillStyle = gradient;
    brush.fill();
    brush.beginPath();
    brush.moveTo(0, -20);
    brush.quadraticCurveTo(3, 2, 0, 20);
    brush.strokeStyle = 'rgba(255,255,255,.42)';
    brush.lineWidth = 1.25;
    brush.stroke();
    return sprite;
  }

  const sprites = [
    makeSprite('#ef8eae', '#ffe3ec'),
    makeSprite('#f4b0c4', '#fff0f5'),
    makeSprite('#d979a0', '#ffdce8')
  ];

  function createPetal(startAnywhere = false) {
    return {
      x: random(0, width),
      y: startAnywhere ? random(-height * .15, height) : random(-130, -20),
      size: random(6, 13),
      speed: random(15, 30),
      drift: random(-5, 10),
      sway: random(14, 32),
      phase: random(0, Math.PI * 2),
      spin: random(-1.05, 1.05),
      rotation: random(0, Math.PI * 2),
      opacity: random(.18, .43),
      sprite: sprites[Math.floor(random(0, sprites.length))]
    };
  }

  function resize() {
    width = window.innerWidth;
    height = window.innerHeight;
    ratio = Math.min(window.devicePixelRatio || 1, 1.5);
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    const count = width < 700 ? 14 : width < 1100 ? 28 : 48;
    petals = Array.from({ length: count }, () => createPetal(true));
  }

  function clear() {
    context.clearRect(0, 0, width, height);
  }

  function draw(time) {
    if (!canAnimate()) {
      frameId = 0;
      clear();
      return;
    }
    const delta = Math.min((time - lastTime) / 1000 || .016, .05);
    lastTime = time;
    clear();

    petals.forEach((petal, index) => {
      petal.y += petal.speed * delta;
      petal.x += (petal.drift + Math.sin(time / 1150 + petal.phase) * petal.sway) * delta;
      petal.rotation += petal.spin * delta;
      if (petal.y > height + 45 || petal.x < -60 || petal.x > width + 60) petals[index] = createPetal(false);

      context.save();
      context.globalAlpha = petal.opacity;
      context.translate(petal.x, petal.y);
      context.rotate(petal.rotation);
      context.drawImage(petal.sprite, -petal.size, -petal.size, petal.size * 2, petal.size * 2);
      context.restore();
    });
    frameId = requestAnimationFrame(draw);
  }

  function start() {
    if (frameId || !canAnimate()) {
      if (!canAnimate()) clear();
      return;
    }
    lastTime = performance.now();
    frameId = requestAnimationFrame(draw);
  }

  function stop() {
    if (frameId) cancelAnimationFrame(frameId);
    frameId = 0;
    clear();
  }

  let resizeFrame = 0;
  window.addEventListener('resize', () => {
    if (resizeFrame) cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(() => { resize(); resizeFrame = 0; });
  }, { passive: true });
  document.addEventListener('visibilitychange', () => { if (canAnimate()) start(); else stop(); });
  window.addEventListener('gteam:motion-change', (event) => { if (event.detail?.enabled) start(); else stop(); });
  const handleReducedMotion = () => { if (canAnimate()) start(); else stop(); };
  if (typeof reducedMotion.addEventListener === 'function') reducedMotion.addEventListener('change', handleReducedMotion);
  else if (typeof reducedMotion.addListener === 'function') reducedMotion.addListener(handleReducedMotion);

  resize();
  start();
})();
