/* Copyright (c) 2026 Ederland Studio — MIT.
 * Origem: ederland/Agentes_IA_3D@073cbc2fdd9c52428e67935e35e0e616c892a258:app/src/office/textures.js
 * alterado: ver static/oficina/README.md
 */
import * as THREE from '../vendor/three/build/three.module.js'

// Texturas procedurales dibujadas en canvas: sin descargas ni imágenes externas
function rng(seed) {
  let s = seed >>> 0
  return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296 }
}

function finish(canvas, repeatX = 1, repeatY = 1) {
  const t = new THREE.CanvasTexture(canvas)
  t.colorSpace = THREE.SRGBColorSpace
  t.wrapS = t.wrapT = THREE.RepeatWrapping
  t.repeat.set(repeatX, repeatY)
  t.anisotropy = 8
  return t
}

const shade = (hex, amt) => {
  const c = new THREE.Color(hex)
  c.offsetHSL(0, 0, amt)
  return `#${c.getHexString()}`
}

// Piso de madera: tablones con variación de tono, vetas y juntas
export function woodTexture(base = '#c89a6a', repeatX = 1, repeatY = 1, seed = 7) {
  const cv = document.createElement('canvas')
  cv.width = cv.height = 512
  const ctx = cv.getContext('2d')
  const r = rng(seed)
  const plankH = 64
  for (let y = 0; y < 512; y += plankH) {
    let x = -Math.floor(r() * 200)
    while (x < 512) {
      const len = 180 + r() * 200
      ctx.fillStyle = shade(base, (r() - 0.5) * 0.09)
      ctx.fillRect(x, y, len, plankH)
      ctx.strokeStyle = 'rgba(60,35,15,0.12)'
      ctx.lineWidth = 1
      for (let v = 0; v < 6; v++) {
        const gy = y + 6 + r() * (plankH - 12)
        ctx.beginPath()
        ctx.moveTo(x, gy)
        ctx.bezierCurveTo(x + len * 0.3, gy + (r() - 0.5) * 6, x + len * 0.7, gy + (r() - 0.5) * 6, x + len, gy)
        ctx.stroke()
      }
      ctx.fillStyle = 'rgba(40,22,8,0.35)'
      ctx.fillRect(x, y, 2, plankH)
      x += len
    }
    ctx.fillStyle = 'rgba(40,22,8,0.3)'
    ctx.fillRect(0, y, 512, 2)
  }
  return finish(cv, repeatX, repeatY)
}

// Baldosas con juntas
export function tileTexture(base = '#d9dde3', grout = '#b9bec7', tiles = 4, repeatX = 1, repeatY = 1, seed = 3) {
  const cv = document.createElement('canvas')
  cv.width = cv.height = 512
  const ctx = cv.getContext('2d')
  const r = rng(seed)
  const size = 512 / tiles
  ctx.fillStyle = grout
  ctx.fillRect(0, 0, 512, 512)
  for (let i = 0; i < tiles; i++) for (let j = 0; j < tiles; j++) {
    ctx.fillStyle = shade(base, (r() - 0.5) * 0.04)
    ctx.fillRect(i * size + 3, j * size + 3, size - 6, size - 6)
  }
  return finish(cv, repeatX, repeatY)
}

// Alfombra con textura de fibra
export function carpetTexture(base = '#6b6f80', repeatX = 1, repeatY = 1, seed = 11) {
  const cv = document.createElement('canvas')
  cv.width = cv.height = 256
  const ctx = cv.getContext('2d')
  const r = rng(seed)
  ctx.fillStyle = base
  ctx.fillRect(0, 0, 256, 256)
  for (let i = 0; i < 5000; i++) {
    ctx.fillStyle = r() > 0.5 ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.07)'
    ctx.fillRect(r() * 256, r() * 256, 1.5, 1.5)
  }
  return finish(cv, repeatX, repeatY)
}

// Estampado frontal del torso del personaje: cuello, costuras y emblema opcional
export function torsoPrint(color, { emblem, seed = 1 } = {}) {
  const cv = document.createElement('canvas')
  cv.width = 128; cv.height = 128
  const ctx = cv.getContext('2d')
  ctx.fillStyle = color
  ctx.fillRect(0, 0, 128, 128)
  // Cuello en V
  ctx.fillStyle = shade(color, -0.12)
  ctx.beginPath(); ctx.moveTo(44, 0); ctx.lineTo(64, 30); ctx.lineTo(84, 0); ctx.fill()
  ctx.strokeStyle = shade(color, -0.2)
  ctx.lineWidth = 2
  ctx.beginPath(); ctx.moveTo(64, 30); ctx.lineTo(64, 128); ctx.stroke()
  // Bolsillo
  if (seed % 2 === 0) { ctx.strokeRect(78, 52, 22, 18) }
  // Cinturón
  ctx.fillStyle = shade(color, -0.25)
  ctx.fillRect(0, 116, 128, 12)
  if (emblem) {
    ctx.fillStyle = emblem
    ctx.shadowColor = emblem
    ctx.shadowBlur = 10
    ctx.beginPath(); ctx.arc(64, 60, 16, 0, Math.PI * 2); ctx.fill()
    ctx.shadowBlur = 0
    ctx.strokeStyle = 'rgba(255,255,255,0.6)'
    ctx.lineWidth = 2
    ctx.beginPath(); ctx.arc(64, 60, 10, 0, Math.PI * 2); ctx.stroke()
  }
  return finish(cv)
}
