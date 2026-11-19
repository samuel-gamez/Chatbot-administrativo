'use strict';
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const esc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const formato = (t) => esc(t).replace(/\*([^*\n]+)\*/g, '<b>$1</b>').replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');

const estado = {
  yo: null, filtro: 'todas', q: '', convId: null, ultimoMsgId: 0, conv: null,
  pendientesPrevias: null, idsPendientes: new Set(), misSinLeer: 0, enviando: false,
  sim: { sesion: String(Date.now()).slice(-6), ultimoId: 0, esperando: false },
};

async function api(url, opciones = {}) {
  const r = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...opciones });
  if (r.status === 401) { location.href = '/login'; throw new Error('sesión'); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : 'Error ' + r.status);
  return j;
}
const post = (url, datos = {}) => api(url, { method: 'POST', body: JSON.stringify(datos) });

function toast(t) {
  const el = $('#toast'); el.textContent = t; el.classList.add('ver');
  clearTimeout(toast._t); toast._t = setTimeout(() => el.classList.remove('ver'), 3200);
}
function hora(iso) {
  if (!iso) return '';
  const d = new Date(iso), hoy = new Date();
  const h = d.toLocaleTimeString('es-CO', { hour: 'numeric', minute: '2-digit' });
  return d.toDateString() === hoy.toDateString() ? h : d.toLocaleDateString('es-CO', { day: 'numeric', month: 'short' }) + ' ' + h;
}
function haceCuanto(iso) {
  const min = Math.round((Date.now() - new Date(iso)) / 60000);
  if (min < 1) return 'ahora'; if (min < 60) return `hace ${min} min`;
  const h = Math.round(min / 60); return h < 24 ? `hace ${h} h` : hora(iso);
}
const iniciales = (c) => (c.nombre || c.telefono).split(/\s+/).slice(0, 2).map((p) => p[0]).join('').toUpperCase();
const telefonoBonito = (t) => t.startsWith('prueba-') ? 'Conversación de prueba' : '+' + t;
const lineaBonita = (l) => l ? 'Línea ' + (l.startsWith('57') && l.length === 12 ? l.slice(2) : l) : '';

/* ---------- alertas ---------- */
let audioCtx;
function sonido() {
  try {
    audioCtx ||= new AudioContext();
    [0, 0.18].forEach((t, i) => {
      const o = audioCtx.createOscillator(), g = audioCtx.createGain();
      o.frequency.value = i ? 1046 : 784; o.connect(g); g.connect(audioCtx.destination);
      g.gain.setValueAtTime(0.18, audioCtx.currentTime + t);
      g.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + t + 0.25);
      o.start(audioCtx.currentTime + t); o.stop(audioCtx.currentTime + t + 0.26);
    });
  } catch { /* sin audio */ }
}
function notificar(titulo, cuerpo) {
  if (!estado.yo?.en_turno) return;
  sonido();
  if ('Notification' in window && Notification.permission === 'granted' && document.hidden) {
    new Notification(titulo, { body: cuerpo, tag: 'admisiones' });
  }
}

/* ---------- turno ---------- */
function pintarTurno() {
  const on = !!estado.yo?.en_turno;
  $('#turno').classList.toggle('on', on);
  $('#turno-texto').textContent = on ? 'En turno' : 'Fuera de turno';
}
$('#turno').addEventListener('click', async () => {
  const nuevo = !estado.yo.en_turno;
  if (nuevo && 'Notification' in window && Notification.permission === 'default') Notification.requestPermission();
  if (nuevo) { try { audioCtx ||= new AudioContext(); audioCtx.resume(); } catch {} }
  const r = await post('/api/turno', { en_turno: nuevo });
  estado.yo.en_turno = r.en_turno; pintarTurno();
  toast(nuevo ? 'Estás en turno: te avisaremos cuando un chat necesite asesor.' : 'Saliste de turno.');
});
$('#salir').addEventListener('click', async () => { await post('/api/logout'); location.href = '/login'; });

/* ---------- vistas ---------- */
$$('.tab').forEach((b) => b.addEventListener('click', () => {
  $$('.tab').forEach((x) => x.classList.toggle('activo', x === b));
  $$('.vista').forEach((v) => v.classList.toggle('activa', v.id === 'vista-' + b.dataset.vista));
  if (b.dataset.vista === 'conocimiento') cargarConocimiento();
  if (b.dataset.vista === 'asesores') cargarAsesores();
}));

/* ---------- bandeja ---------- */
$$('#filtros .chip').forEach((b) => b.addEventListener('click', () => {
  estado.filtro = b.dataset.f;
  $$('#filtros .chip').forEach((x) => x.classList.toggle('activo', x === b));
  cargarBandeja();
}));
let tBuscar;
$('#buscar').addEventListener('input', (e) => { clearTimeout(tBuscar); tBuscar = setTimeout(() => { estado.q = e.target.value; cargarBandeja(); }, 250); });

async function cargarBandeja() {
  let d;
  try { d = await api(`/api/bandeja?filtro=${estado.filtro}&q=${encodeURIComponent(estado.q)}`); }
  catch (e) { return; }
  estado.yo = d.yo; pintarTurno();
  const c = d.contadores;
  $('#n-pend').textContent = c.pendientes; $('#n-pend').dataset.v = c.pendientes;
  $('#n-mias').textContent = c.mias_sin_leer || ''; $('#n-mias').dataset.v = c.mias_sin_leer;
  document.title = (c.pendientes + c.mias_sin_leer ? `(${c.pendientes + c.mias_sin_leer}) ` : '') + 'Panel de asesores';

  // alertas: nuevas solicitudes de asesor o mensajes nuevos en mis chats
  if (estado.pendientesPrevias !== null && c.pendientes > estado.pendientesPrevias) {
    notificar('Un aspirante necesita asesor', 'Hay ' + c.pendientes + ' chat(s) esperando.');
  } else if (c.mias_sin_leer > estado.misSinLeer && estado.pendientesPrevias !== null) {
    notificar('Mensaje nuevo', 'Un aspirante que atiendes te escribió.');
  }
  estado.pendientesPrevias = c.pendientes; estado.misSinLeer = c.mias_sin_leer;

  const items = $('#items');
  if (!d.conversaciones.length) {
    const textos = { pendientes: 'No hay chats esperando asesor. 🎉', mias: 'No tienes chats tomados.' };
    items.innerHTML = `<div class="vacio">${textos[estado.filtro] || 'No hay conversaciones.'}</div>`;
    return;
  }
  items.innerHTML = d.conversaciones.map((cv) => {
    const alerta = cv.necesita_asesor && cv.modo === 'bot';
    let badge = cv.modo === 'humano'
      ? `<span class="badge humano">${cv.asesor && cv.asesor.id === estado.yo.id ? 'Tú' : esc(cv.asesor?.nombre || 'Asesor')}</span>`
      : alerta ? '<span class="badge alerta">Necesita asesor</span>' : '<span class="badge bot">IA</span>';
    if (cv.es_simulada) badge += ' <span class="badge prueba">Prueba</span>';
    const previa = cv.ultimo ? (cv.ultimo.origen === 'usuario' ? '' : cv.ultimo.origen === 'bot' ? 'IA: ' : 'Asesor: ') + cv.ultimo.texto : '';
    const noLeidos = cv.no_leidos && (cv.modo === 'bot' || cv.asesor?.id === estado.yo.id) ? `<span class="no-leidos">${cv.no_leidos}</span>` : '';
    return `<div class="item ${cv.id === estado.convId ? 'sel' : ''} ${alerta ? 'alerta' : ''}" data-id="${cv.id}">
      <div class="avatar">${esc(iniciales(cv))}</div>
      <div style="min-width:0"><div class="nombre">${esc(cv.nombre || telefonoBonito(cv.telefono))}</div>
        <div class="previa">${alerta ? '🔔 ' + esc(cv.motivo || '') : esc(previa)}</div></div>
      <div class="meta"><span>${haceCuanto(cv.ultima_actividad)}</span>${cv.linea ? `<span class="linea">${esc(lineaBonita(cv.linea))}</span>` : ''}<div>${badge}</div>${noLeidos}</div>
    </div>`;
  }).join('');
  items.querySelectorAll('.item').forEach((el) => el.addEventListener('click', () => abrirChat(+el.dataset.id)));
}

/* ---------- chat abierto ---------- */
function plantillaChat() {
  $('#chat').innerHTML = `
    <div class="chat-cab">
      <button class="btn volver" id="volver">←</button>
      <div class="avatar" id="c-avatar"></div>
      <div><div class="titulo" id="c-nombre"></div><div class="sub" id="c-sub"></div></div>
      <div class="acciones" id="c-acciones"></div>
    </div>
    <div class="banner" id="c-banner"></div>
    <div class="mensajes" id="c-mensajes"></div>
    <div>
      <form class="compositor" id="c-form">
        <textarea id="c-texto" rows="1" placeholder="Escribe tu respuesta al aspirante…"></textarea>
        <button class="btn primario" id="c-enviar">Enviar</button>
      </form>
      <div class="pista" id="c-pista"></div>
    </div>`;
  $('#volver').addEventListener('click', () => { $('#vista-chats').classList.remove('con-chat'); estado.convId = null; cargarBandeja(); });
  const ta = $('#c-texto');
  ta.addEventListener('input', () => { ta.style.height = 'auto'; ta.style.height = ta.scrollHeight + 'px'; });
  ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('#c-form').requestSubmit(); } });
  $('#c-form').addEventListener('submit', enviarMensaje);
}

async function abrirChat(id) {
  estado.convId = id; estado.ultimoMsgId = 0; estado.conv = null;
  plantillaChat();
  $('#vista-chats').classList.add('con-chat');
  $$('.item').forEach((el) => el.classList.toggle('sel', +el.dataset.id === id));
  await refrescarChat(true);
}

function pintarCabecera(c) {
  const mio = c.modo === 'humano' && c.asesor?.id === estado.yo.id;
  $('#c-avatar').textContent = iniciales(c);
  $('#c-nombre').textContent = c.nombre || telefonoBonito(c.telefono);
  $('#c-sub').textContent = [c.nombre ? telefonoBonito(c.telefono) : '', lineaBonita(c.linea),
    c.modo === 'humano' ? `Atiende: ${mio ? 'tú' : c.asesor?.nombre}` : 'Atiende: IA'].filter(Boolean).join(' · ');

  const acc = [];
  if (c.modo === 'bot') acc.push('<button class="btn primario" data-a="tomar">Tomar chat</button>');
  if (c.modo === 'humano' && !mio) acc.push('<button class="btn" data-a="tomar">Tomar de todos modos</button>');
  if (c.modo === 'humano' && (mio || estado.yo.es_admin)) acc.push('<button class="btn" data-a="devolver">Devolver a la IA</button>');
  if (c.necesita_asesor && c.modo === 'bot') acc.push('<button class="btn" data-a="descartar" title="La IA ya lo resolvió">Marcar atendido</button>');
  if (!(c.modo === 'humano' && !mio && !estado.yo.es_admin)) acc.push('<button class="btn peligro" data-a="eliminar" title="Borra este chat del panel">Eliminar</button>');
  $('#c-acciones').innerHTML = acc.join('');
  $('#c-acciones').querySelectorAll('button').forEach((b) => b.addEventListener('click', () => accion(b.dataset.a)));

  const banner = $('#c-banner');
  if (c.necesita_asesor && c.modo === 'bot') {
    banner.className = 'banner alerta';
    banner.textContent = `🔔 La IA pidió un asesor ${c.solicitado_en ? haceCuanto(c.solicitado_en) : ''}: ${c.motivo || ''}`;
  } else if (!c.ventana_24h) {
    banner.className = 'banner error';
    banner.textContent = 'Pasaron más de 24 h desde el último mensaje del aspirante: WhatsApp no deja escribirle hasta que vuelva a escribir.';
  } else if (mio) {
    banner.className = 'banner info';
    banner.textContent = 'Estás atendiendo este chat. La IA no responde hasta que lo devuelvas.';
  } else banner.className = 'banner';

  $('#c-pista').textContent = mio ? 'Enter envía · Shift+Enter nueva línea'
    : c.modo === 'humano' ? `Lo atiende ${c.asesor?.nombre}. Si escribes, tomarás el chat.`
    : 'Si escribes, tomarás el chat y la IA dejará de responder.';
  $('#c-enviar').disabled = !c.ventana_24h;
}

function pintarMensajes(lista, contenedor, modoSim = false) {
  const atBottom = contenedor.scrollHeight - contenedor.scrollTop - contenedor.clientHeight < 80;
  for (const m of lista) {
    const el = document.createElement('div');
    if (m.origen === 'sistema') {
      el.className = 'sistema'; el.textContent = m.texto;
    } else if (modoSim) {
      el.className = 'burbuja ' + (m.origen === 'usuario' ? 'yo' : 'ellos');
      el.innerHTML = `${m.origen === 'asesor' ? `<div class="autor" style="color:var(--verde)">${esc(m.asesor)} · asesor</div>` : ''}<div class="txt">${formato(m.texto)}</div><div class="hora">${hora(m.creado)}</div>`;
    } else {
      el.className = 'burbuja ' + m.origen;
      const autor = m.origen === 'bot' ? 'IA' : m.origen === 'asesor' ? (m.asesor || 'Asesor') : '';
      el.innerHTML = `${autor ? `<div class="autor">${esc(autor)}</div>` : ''}<div class="txt">${formato(m.texto)}</div>
        ${m.error ? `<div class="fallo">⚠ No entregado: ${esc(m.error)}</div>` : ''}<div class="hora">${hora(m.creado)}</div>`;
    }
    contenedor.appendChild(el);
  }
  if (lista.length && (atBottom || contenedor.dataset.inicial !== '1')) contenedor.scrollTop = contenedor.scrollHeight;
  contenedor.dataset.inicial = '1';
}

async function refrescarChat() {
  const id = estado.convId; if (!id) return;
  let d;
  try { d = await api(`/api/conversaciones/${id}?desde=${estado.ultimoMsgId}`); } catch (e) { return; }
  if (id !== estado.convId) return;
  estado.conv = d.conversacion;
  pintarCabecera(d.conversacion);
  if (d.mensajes.length) {
    pintarMensajes(d.mensajes, $('#c-mensajes'));
    estado.ultimoMsgId = d.mensajes[d.mensajes.length - 1].id;
  }
}

async function accion(a) {
  if (a === 'eliminar') return eliminarChat();
  try {
    const r = await post(`/api/conversaciones/${estado.convId}/${a}`);
    if (a === 'tomar' && r.presentacion) {
      const ta = $('#c-texto');
      if (ta && !ta.value.trim()) {
        ta.value = r.presentacion;
        ta.style.height = 'auto'; ta.style.height = ta.scrollHeight + 'px';
      }
    }
    toast({ tomar: 'Tomaste el chat. Revisa el mensaje de presentación y envíalo con Enter.', devolver: 'La IA vuelve a atender este chat.', descartar: 'Solicitud marcada como atendida.' }[a]);
    if (a === 'tomar') $('#c-texto')?.focus();
  } catch (e) { toast(e.message); }
  refrescarChat(); cargarBandeja();
}

async function eliminarChat() {
  const c = estado.conv;
  const nombre = c ? (c.nombre || telefonoBonito(c.telefono)) : 'este chat';
  if (!confirm(`¿Eliminar el chat con ${nombre}?\n\nSe borran todos sus mensajes del panel y no se puede deshacer. ` +
               `(En el WhatsApp de la persona la conversación sigue igual.)`)) return;
  try {
    await api(`/api/conversaciones/${estado.convId}`, { method: 'DELETE' });
    toast('Chat eliminado.');
    estado.convId = null; estado.conv = null;
    $('#vista-chats').classList.remove('con-chat');
    $('#chat').innerHTML = '<div class="sin-chat" id="sin-chat"><div><b>Selecciona un chat</b>Los chats que la IA marque como "necesita asesor" aparecen primero.</div></div>';
  } catch (e) { toast(e.message); }
  cargarBandeja();
}

async function enviarMensaje(e) {
  e.preventDefault();
  const ta = $('#c-texto'), texto = ta.value.trim();
  if (!texto || estado.enviando) return;
  estado.enviando = true; $('#c-enviar').disabled = true;
  try {
    await post(`/api/conversaciones/${estado.convId}/enviar`, { texto });
    ta.value = ''; ta.style.height = 'auto';
  } catch (err) { toast(err.message); }
  estado.enviando = false; $('#c-enviar').disabled = false;
  refrescarChat(); cargarBandeja();
}

/* ---------- simulador ---------- */
const simTel = () => `prueba-${estado.yo.id}-${estado.sim.sesion}`;
$('#sim-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const ta = $('#sim-texto'), texto = ta.value.trim(); if (!texto) return;
  ta.value = '';
  pintarMensajes([{ origen: 'usuario', texto, creado: new Date().toISOString() }], $('#sim-mensajes'), true);
  estado.sim.ultimoId = Infinity; // evitar duplicar mientras llega del servidor
  estado.sim.esperando = true; $('#sim-escribiendo').textContent = 'escribiendo…';
  try { await post('/api/simular', { texto, sesion: estado.sim.sesion }); } catch (err) { toast(err.message); }
  setTimeout(() => refrescarSim(true), 400);
});
$('#sim-texto').addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('#sim-form').requestSubmit(); } });
$('#sim-reiniciar').addEventListener('click', async () => {
  await api('/api/simular/' + simTel(), { method: 'DELETE' }).catch(() => {});
  estado.sim = { sesion: String(Date.now()).slice(-6), ultimoId: 0, esperando: false };
  $('#sim-mensajes').innerHTML = ''; $('#sim-escribiendo').textContent = '';
});
async function refrescarSim(completo = false) {
  if (!estado.yo) return;
  let d; try { d = await api('/api/simular/' + simTel()); } catch { return; }
  const cont = $('#sim-mensajes');
  if (completo || estado.sim.ultimoId === Infinity) { cont.innerHTML = ''; estado.sim.ultimoId = 0; }
  const nuevos = d.mensajes.filter((m) => m.id > estado.sim.ultimoId);
  if (nuevos.length) { pintarMensajes(nuevos, cont, true); estado.sim.ultimoId = nuevos[nuevos.length - 1].id; }
  const ultimo = d.mensajes[d.mensajes.length - 1];
  const esperaIA = ultimo && ultimo.origen === 'usuario' && d.conversacion?.modo === 'bot';
  $('#sim-escribiendo').textContent = esperaIA ? 'escribiendo…' : '';
}

/* ---------- conocimiento ---------- */
async function cargarConocimiento() {
  const p = await api('/api/presentacion');
  $('#presentacion').value = p.texto;
  $('#presentacion').readOnly = !estado.yo.es_admin;
  $('#guardar-presentacion').hidden = !estado.yo.es_admin;
  const d = await api('/api/conocimiento');
  $('#conocimiento').value = d.texto;
  $('#conocimiento').readOnly = !estado.yo.es_admin;
  $('#guardar-conocimiento').hidden = !estado.yo.es_admin;
  $('#cargar-archivo').hidden = !estado.yo.es_admin;
  $('#estado-conocimiento').textContent = (estado.yo.es_admin ? '' : 'Solo un administrador puede editar. ') +
    (d.actualizado ? 'Última actualización: ' + hora(d.actualizado) : '');
}
$('#guardar-conocimiento').addEventListener('click', async () => {
  try { await api('/api/conocimiento', { method: 'PUT', body: JSON.stringify({ texto: $('#conocimiento').value }) });
    $('#estado-conocimiento').textContent = 'Guardado ✓ — la IA ya usa esta información.'; }
  catch (e) { toast(e.message); }
});

$('#guardar-presentacion').addEventListener('click', async () => {
  try { await api('/api/presentacion', { method: 'PUT', body: JSON.stringify({ texto: $('#presentacion').value }) });
        $('#estado-presentacion').textContent = 'Guardado ✓'; }
  catch (e) { toast(e.message); }
});

$('#probar-ia').addEventListener('click', async () => {
  const el = $('#resultado-ia'); el.className = 'aviso ok'; el.textContent = 'Probando…';
  try {
    const r = await api('/api/diagnostico-ia');
    if (r.ok) { el.className = 'aviso ok'; el.textContent = `✓ La IA funciona (modelo ${r.modelo}). Respuesta: "${r.respuesta}"`; }
    else { el.className = 'aviso error'; el.textContent = '✗ La IA no responde: ' + r.error; }
  } catch (e) { el.className = 'aviso error'; el.textContent = e.message; }
});

$('#cargar-archivo').addEventListener('click', async () => {
  if (!confirm('¿Reemplazar la información actual por la del archivo conocimiento/universidad.md?')) return;
  try { const r = await post('/api/conocimiento/desde-archivo'); await cargarConocimiento();
        toast(`Información cargada (${r.caracteres} caracteres). La IA ya la usa.`); }
  catch (e) { toast(e.message); }
});

/* ---------- asesores ---------- */
async function cargarAsesores() {
  $('#url-registro').textContent = location.origin + '/registro';
  const lista = await api('/api/asesores');
  const etiquetas = { pendiente: '<span class="badge alerta">Pendiente</span>', activo: '<span class="badge humano">Activo</span>', inactivo: '<span class="badge bot">Inactivo</span>' };
  $('#tabla-asesores').innerHTML = lista.map((a) => {
    const yo = a.id === estado.yo.id; const b = [];
    if (a.estado === 'pendiente') b.push(`<button class="btn primario" data-id="${a.id}" data-c='{"estado":"activo"}'>Aprobar</button>`, `<button class="btn peligro" data-id="${a.id}" data-c='{"estado":"inactivo"}'>Rechazar</button>`);
    if (a.estado === 'activo' && !yo) b.push(`<button class="btn peligro" data-id="${a.id}" data-c='{"estado":"inactivo"}'>Desactivar</button>`);
    if (a.estado === 'inactivo') b.push(`<button class="btn" data-id="${a.id}" data-c='{"estado":"activo"}'>Reactivar</button>`);
    if (a.estado === 'activo' && !yo) b.push(`<button class="btn" data-id="${a.id}" data-c='{"es_admin":${!a.es_admin}}'>${a.es_admin ? 'Quitar admin' : 'Hacer admin'}</button>`);
    if (a.estado === 'activo' && !yo) b.push(`<button class="btn" data-reset="${a.id}">Restablecer clave</button>`);
    return `<tr><td><b>${esc(a.nombre)}</b>${yo ? ' (tú)' : ''}${a.en_turno ? ' <span class="badge humano">En turno</span>' : ''}</td>
      <td>${esc(a.email)}</td><td>${etiquetas[a.estado]}</td><td>${a.es_admin ? 'Administrador' : 'Asesor'}</td>
      <td style="text-align:right;white-space:nowrap">${b.join(' ')}</td></tr>`;
  }).join('');
  $$('#tabla-asesores button[data-reset]').forEach((btn) => btn.addEventListener('click', async () => {
    try {
      const r = await post(`/api/asesores/${btn.dataset.reset}/restablecer`);
      const av = $('#aviso-clave');
      av.textContent = `Contraseña temporal de ${r.nombre}: ${r.temporal} — dásela en persona; puede cambiarla en "Mi cuenta".`;
      av.style.display = 'block';
    } catch (e) { toast(e.message); }
  }));
  $$('#tabla-asesores button[data-c]').forEach((btn) => btn.addEventListener('click', async () => {
    try { await post('/api/asesores/' + btn.dataset.id, JSON.parse(btn.dataset.c)); } catch (e) { toast(e.message); }
    cargarAsesores();
  }));
}

/* ---------- mi cuenta ---------- */
$('#form-clave').addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    await post('/api/mi-clave', { actual: $('#clave-actual').value, nueva: $('#clave-nueva').value });
    e.target.reset(); toast('Contraseña actualizada.');
  } catch (err) { toast(err.message.includes('Error 422') ? 'La nueva contraseña debe tener al menos 8 caracteres.' : err.message); }
});

/* ---------- arranque ---------- */
(async function iniciar() {
  estado.yo = await api('/api/yo');
  $('#yo-nombre').textContent = estado.yo.nombre;
  $('#cuenta-datos').textContent = `${estado.yo.nombre} · ${estado.yo.email} · ${estado.yo.es_admin ? 'Administrador' : 'Asesor'}`;
  $('#tab-asesores').hidden = !estado.yo.es_admin;
  pintarTurno();
  await cargarBandeja();
  setInterval(cargarBandeja, 4000);
  setInterval(() => { refrescarChat(); }, 2000);
  setInterval(() => { if ($('#vista-prueba').classList.contains('activa')) refrescarSim(); }, 800);
})();
