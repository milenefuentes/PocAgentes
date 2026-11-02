
function formatearRazonamiento(texto) {
  if (!texto || texto === "—") return '<p class="m-sec-text texto-tenue">—</p>';

  let t = String(texto).trim();
  let intro = "";
  const matchIntro = t.match(/^(Análisis paso a paso:?|Razonamiento paso a paso:?|Razonamiento:?)\s*/i);
  if (matchIntro) {
    intro = matchIntro[1];
    t = t.slice(matchIntro[0].length).trim();
  }

  let conclusion = "";
  const matchConc = t.match(/\s*(Clasificación:?.*)$/i);
  if (matchConc) {
    conclusion = matchConc[1].trim();
    t = t.slice(0, matchConc.index).trim();
  }

  // Detectar pasos numerados: 1) o 1. (sin confundir con decimales como 78.2%)
  const regex = /(?:^|\s)(?=\d+\)\s|\d+\.\s+(?!\d))/g;
  const partes = t.split(regex).map(s => s.trim()).filter(Boolean);

  if (partes.length > 1) {
    const pasosHtml = partes.map(p => {
      const m = p.match(/^(\d+)[\)\.]\s*(.*)/s);
      if (m) {
        const num = String(m[1]).padStart(2, "0");
        const cuerpo = esc(m[2]);
        return `
          <div class="razonamiento-paso">
            <span class="paso-num">${num}</span>
            <div class="paso-cuerpo">${cuerpo}</div>
          </div>`;
      }
      return `<div class="razonamiento-parrafo">${esc(p)}</div>`;
    }).join("");

    let html = "";
    if (intro) {
      html += `<div class="razonamiento-encabezado">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
        <span>${esc(intro)}</span>
      </div>`;
    }
    html += `<div class="razonamiento-lista">${pasosHtml}</div>`;
    if (conclusion) {
      html += `
        <div class="razonamiento-dictamen">
          <span class="dictamen-ico"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><polyline points="9 12 11 14 15 10"/></svg></span>
          <div class="dictamen-texto"><strong>Dictamen:</strong> ${esc(conclusion)}</div>
        </div>`;
    }
    return html;
  }

  return `<p class="m-sec-text">${esc(texto).replace(/\n\n+/g, '</p><p class="m-sec-text">')}</p>`;
}

/**
 * PORTAL WEB MVP - CLAIMS AI (INTERBANK)
 * Control de Interfaz, Datos en Vivo y Grafo de Red Interactivo
 */

// Estado global de la aplicación
const AppState = {
  data: null,
  patrones: [],
  casos: [],
  casosPorId: {},
  selectedPatron: null,
  grafoCentro: null,
};

// ============================================================================
// INICIALIZACIÓN
// ============================================================================
document.addEventListener("DOMContentLoaded", () => {
  cargarDatosEnVivo();
  configurarEventosUI();
});

// Cargar datos en vivo desde el servidor Python
async function cargarDatosEnVivo() {
  try {
    const res = await fetch("/api/data");
    if (!res.ok) throw new Error("Error HTTP " + res.status);
    const data = await res.json();
    AppState.data = data;
    AppState.patrones = data.patrones || [];

    renderizarKPIs(data.resumen_ejecutivo);
    renderizarTablaPatrones(data.patrones || []);
    renderizarTablaHipotesis(data.hipotesis || []);
    poblarSelectorValidacion(data.hipotesis || []);
    renderizarEmbudo(data.embudo);
    await cargarCasos();
    renderizarHallazgo(data.patrones || [], data.embudo);
    renderizarGraficos(data.patrones || [], AppState.casos);
    renderizarCalidad(data.calidad_a1);
    renderizarResumen(data, AppState.casos);
    poblarFiltrosGrafo();
    if (!document.querySelector('.vista[data-vista="red"]').hidden) renderizarGrafo();
    if (AppState.patronPendiente) { const id = AppState.patronPendiente; AppState.patronPendiente = null; abrirPatron(id); }

    if (data.servidor && data.servidor.db_host) {
      const host = data.servidor.db_host;
      const el = document.getElementById("db-source-name");
      el.textContent = host.includes("neon") ? "Base de datos: Neon" : "Base de datos: " + host;
      el.title = "Conectado a " + host;
    }
  } catch (err) {
    console.error("Error al cargar datos en vivo:", err);
  }
}

// ============================================================================
// RENDERIZADO DE COMPONENTES
// ============================================================================

// Los textos vienen del LLM: se escapan antes de insertarlos como HTML
function esc(valor) {
  return String(valor ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

function formatoPct(valor) {
  return valor === null || valor === undefined ? "—" : Math.round(parseFloat(valor)) + "%";
}

// Etiqueta de Pattern Memory: Nuevo / Conocido / Bajo umbral
function chipMemoria(item) {
  if (!item || !item.clasificacion) return "";
  const clase = { "Nuevo": "nuevo", "Conocido": "conocido", "Bajo umbral": "bajo-umbral" }[item.clasificacion] || "";
  const heredada = item.decision_heredada ? ' <span class="chip-memoria heredada" title="Hereda la decisión previa del analista (sin evidencia nueva)">decisión heredada</span>' : "";
  return `<span class="chip-memoria ${clase}">${esc(item.clasificacion)}</span>${heredada}`;
}

function nivelImpacto(monto) {
  return monto > 15000 ? "Alto" : (monto > 5000 ? "Medio" : "Bajo");
}

function renderizarKPIs(resumen) {
  if (!resumen) return;
  const el = document.getElementById("ultima-act");
  el.innerHTML = resumen.corrida_fecha
    ? `<span class="dot-ok"></span>Última actualización: ${esc(fechaDMY(resumen.corrida_fecha))} ${esc(horaHM(resumen.corrida_fecha))} · corrida ${esc(resumen.corrida_id)}`
    : "";
}

// ============================================================================
// RESUMEN: INDICADORES, CASOS POR MES Y PATRONES DE LA CORRIDA
// ============================================================================
const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];

// Cuenta los casos por mes de la llamada; devuelve los últimos n meses (0 = todo el periodo)
function casosPorMes(casos, n) {
  const conteo = {};
  casos.forEach(c => { if (c.fecha_llamada) { const k = String(c.fecha_llamada).slice(0, 7); conteo[k] = (conteo[k] || 0) + 1; } });
  const claves = Object.keys(conteo).sort();
  if (!claves.length) return [];
  const hoy = new Date();
  const fin = n ? `${hoy.getFullYear()}-${String(hoy.getMonth() + 1).padStart(2, "0")}` : claves[claves.length - 1];
  let [a, m] = (n ? fin : claves[0]).split("-").map(Number);
  if (n) { m -= n - 1; while (m < 1) { m += 12; a -= 1; } }
  const meses = [];
  while (`${a}-${String(m).padStart(2, "0")}` <= fin) {
    const k = `${a}-${String(m).padStart(2, "0")}`;
    meses.push({ clave: k, etiqueta: `${MESES[m - 1]} ${String(a).slice(2)}`, valor: conteo[k] || 0 });
    if (++m > 12) { m = 1; a += 1; }
  }
  return meses;
}

function renderizarResumen(data, casos) {
  const resumen = data.resumen_ejecutivo || {};
  const embudo = data.embudo || {};
  const sinHistorico = '<span class="sin-dato">Sin histórico para comparar</span>';

  // 1. Casos analizados, con la variación del último mes cerrado frente al anterior
  const seis = casosPorMes(casos, 6);
  document.getElementById("k-casos").textContent = casos.length;
  const ult = seis[seis.length - 2], ant = seis[seis.length - 3];   // meses completos (el actual va en curso)
  document.getElementById("k-casos-sub").innerHTML = ult && ant && ant.valor
    ? (() => { const v = Math.round((ult.valor - ant.valor) / ant.valor * 100);
        return `<span class="tendencia ${v >= 0 ? "sube" : "baja"}">${v >= 0 ? "▲ +" : "▼ "}${v} %</span> ${esc(ult.etiqueta)} vs. ${esc(ant.etiqueta)}`; })()
    : sinHistorico;
  const max = Math.max(...seis.map(m => m.valor), 1);
  document.getElementById("k-casos-mini").innerHTML = seis.map(m =>
    `<span style="height:${Math.max(8, m.valor / max * 100)}%" title="${esc(m.etiqueta)}: ${m.valor}"></span>`).join("");

  // 2. Patrones nuevos de la corrida
  document.getElementById("k-nuevos").textContent = (embudo.clasificacion || {})["Nuevo"] || 0;
  document.getElementById("k-nuevos-sub").textContent =
    `de ${resumen.patrones_identificados ?? 0} patrones en la corrida ${resumen.corrida_id ?? "—"}`;

  // 3. Pendientes de validar (hipótesis) y casos sin revisar
  const sinRevisar = casos.filter(c => (c.estado_revision || "Pendiente") === "Pendiente").length;
  document.getElementById("k-pendientes").textContent = resumen.hipotesis_pendientes ?? 0;
  document.getElementById("k-pendientes-sub").textContent =
    `patrones nuevos · ${sinRevisar} casos sin revisar`;
  document.getElementById("k-pendientes-sub").title =
    `${resumen.hipotesis_registradas ?? 0} patrones conocidos o bajo umbral quedan registrados sin pedir decisión`;
  renderizarEjecucion(data.ejecucion);

  // 4. Casos incompletos (variante 3 de la ficha)
  const incompletos = casos.filter(c => c.variante === 3).length;
  document.getElementById("k-incompletos").textContent = incompletos;
  document.getElementById("k-incompletos-sub").textContent =
    `${casos.length ? Math.round(incompletos / casos.length * 100) : 0} % de los casos · ficha variante 3`;
  ["k-nuevos-mini", "k-pendientes-mini", "k-incompletos-mini"].forEach(id => document.getElementById(id).innerHTML = "");

  renderizarCasosPorMes();

  // Tabla de patrones de la corrida
  document.getElementById("tabla-corrida-nota").textContent =
    resumen.corrida_id ? `Corrida ${resumen.corrida_id} · ${resumen.corrida_casos} casos` : "Sin corridas";
  const tbody = document.getElementById("tbody-corrida");
  tbody.innerHTML = "";
  (data.patrones || []).forEach((p, idx) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="col-patron" title="${esc(p.titulo)}"><span class="recorte-2">${esc(p.titulo)}</span></td>
      <td>${p.num_casos || 0}</td>
      <td class="nowrap">${esc(formatoSoles(p.impacto_total))}</td>
      <td>${formatoPct(p.confianza)}</td>
      <td>${chipEstadoPatron(p)}</td>`;
    tr.addEventListener("click", () => abrirModalDetalle(p, "P" + String(idx + 1).padStart(2, "0")));
    tbody.appendChild(tr);
  });
}

// Estado de la ejecución: última corrida de cada agente y lo que falta procesar
function renderizarEjecucion(ej) {
  const cont = document.getElementById("ejecucion");
  if (!ej) { cont.innerHTML = ""; return; }
  const a1 = ej.a1
    ? `Corrida ${esc(ej.a1.corrida_a1_id)} · ${esc(fechaDMY(ej.a1.inicio))} ${esc(horaHM(ej.a1.inicio))} · ${esc(ej.a1.n_casos ?? "—")} casos` +
      (ej.a1.fin ? (ej.a1.n_fallidos ? ` · <span class="ej-alerta">${esc(ej.a1.n_fallidos)} fallidos</span>` : " · sin fallos") : ' · <span class="ej-alerta">en curso o interrumpida</span>')
    : ej.a1_ultimo_analisis
      ? `Último análisis ${esc(fechaDMY(ej.a1_ultimo_analisis))} ${esc(horaHM(ej.a1_ultimo_analisis))} · ${esc(ej.a1_casos_ultimo_dia)} casos <span class="sin-dato">(antes del registro por corrida)</span>`
      : '<span class="sin-dato">Sin análisis</span>';
  const a2 = ej.a2 && ej.a2.corrida_id
    ? `Corrida ${esc(ej.a2.corrida_id)} · ${esc(fechaDMY(ej.a2.fecha))} ${esc(horaHM(ej.a2.fecha))} · ${esc(ej.a2.n_casos)} casos · completada`
    : '<span class="sin-dato">Sin corridas</span>';
  cont.innerHTML = `
    <span class="ej-titulo">Estado de la ejecución</span>
    <span class="ej-item"><span class="ej-dot ${ej.a1 && !ej.a1.fin ? "amarillo" : "verde"}"></span><strong>Agente 1</strong> ${a1}</span>
    <span class="ej-item"><span class="ej-dot verde"></span><strong>Agente 2</strong> ${a2}</span>
    <span class="ej-item"><span class="ej-dot ${ej.llamadas_pendientes ? "amarillo" : "verde"}"></span>
      <strong>${esc(ej.llamadas_pendientes)}</strong> llamadas pendientes de analizar</span>`;
}

// Estado del patrón: la decisión del analista manda; si no hay, se muestra si es nuevo o conocido
function chipEstadoPatron(p) {
  const decision = { "Validado": ["Validado", "verde"], "En Investigación": ["En revisión", "azul"],
                     "Descartado": ["Descartado", "ambar"] }[p.estado_validacion];
  const sintesis = { "Nuevo": ["Nuevo", "verde"], "Conocido": ["Conocido", "gris"], "Bajo umbral": ["Bajo umbral", "ambar"] };
  const [texto, color] = decision || sintesis[p.clasificacion] || ["Pendiente", "gris"];
  return `<span class="estado-punto ${color}">${texto}</span>`;
}

function renderizarCasosPorMes() {
  const n = parseInt(document.getElementById("periodo-casos").value, 10);
  let meses = casosPorMes(AppState.casos, n);
  // Todo el periodo: se agrupa por trimestre para que las barras se puedan leer
  if (!n) {
    const trimestres = [];
    meses.forEach(m => {
      const [a, mm] = m.clave.split("-").map(Number);
      const t = Math.ceil(mm / 3), clave = `${a}-T${t}`;
      const ultimo = trimestres[trimestres.length - 1];
      if (ultimo && ultimo.clave === clave) { ultimo.valor += m.valor; ultimo.meses.push(m.clave); }
      else trimestres.push({ clave, etiqueta: `T${t} ${String(a).slice(2)}`, valor: m.valor, meses: [m.clave] });
    });
    meses = trimestres;
  }
  const cont = document.getElementById("grafico-meses");
  const max = Math.max(...meses.map(m => m.valor), 1);
  const paso = Math.max(1, Math.ceil(meses.length / 8));   // con muchos meses, no se rotula cada barra
  const colores = ["navy", "azul", "celeste", "verde"];
  cont.innerHTML = meses.length ? meses.map((m, k) => `
    <div class="col" data-mes="${(m.meses || [m.clave]).join(",")}" title="${esc(m.etiqueta)}: ${m.valor} caso${m.valor === 1 ? "" : "s"}">
      <span class="col-valor">${m.valor || ""}</span>
      <span class="col-barra ${colores[k % colores.length]}" style="height:${m.valor / max * 100}%"></span>
      <span class="col-etq">${k % paso === 0 ? esc(m.etiqueta) : ""}</span>
    </div>`).join("") : '<p class="texto-tenue">Sin casos.</p>';
  cont.querySelectorAll(".col").forEach(col => col.addEventListener("click", () => {
    document.getElementById("casos-buscar").value = "";
    AppState.filtroMes = col.dataset.mes;
    mostrarVista("reclamos");
    renderizarTablaCasos();
  }));
}

function renderizarTablaPatrones(patrones) {
  const tbody = document.getElementById("tbody-patrones");
  tbody.innerHTML = "";
  const filtro = AppState.filtroSintesis;
  const visibles = patrones.filter(p => !filtro || p.clasificacion === filtro).length;
  document.getElementById("count-patrones-badge").textContent =
    filtro ? `${visibles} de ${patrones.length}` : `${patrones.length} encontrados`;

  patrones.forEach((p, idx) => {
    if (filtro && p.clasificacion !== filtro) return;
    const tr = document.createElement("tr");
    const pId = "P" + String(idx + 1).padStart(2, "0");
    const impactoTexto = p.nivel_impacto || nivelImpacto(parseFloat(p.impacto_total || 0));

    tr.innerHTML = `
      <td><span class="patron-id-chip">${pId}</span></td>
      <td>
        <span class="patron-title">${chipMemoria(p)} ${esc(p.titulo)}</span>
        <span class="patron-sub">${esc(p.descripcion)}</span>
      </td>
      <td><strong style="color: var(--text-strong);">${p.num_casos || 0}</strong></td>
      <td><strong style="color: #0369a1;">${formatoPct(p.confianza)}</strong></td>
      <td><span class="badge-impacto ${impactoTexto.toLowerCase()}">${impactoTexto}</span></td>
      <td>${chipPrioridad(p.prioridad, p.prioridad_detalle)}</td>
    `;

    tr.addEventListener("click", () => abrirModalDetalle(p, pId));
    tbody.appendChild(tr);
  });
}

function renderizarTablaHipotesis(hipotesis) {
  const tbody = document.getElementById("tbody-hipotesis");
  tbody.innerHTML = "";

  const filtro = AppState.filtroSintesis;
  hipotesis.forEach((h, idx) => {
    if (filtro && h.clasificacion !== filtro) return;
    const tr = document.createElement("tr");
    const hId = "H" + (idx + 1);
    // Solo lo nuevo requiere decisión; lo conocido o bajo umbral queda registrado (lámina 18)
    const registrado = (h.estado || "Pendiente revisión") === "Pendiente revisión" && h.clasificacion !== "Nuevo";
    const estado = registrado ? "Registrado · no requiere decisión" : (h.estado || "Pendiente revisión");
    const estadoClase = estado.includes("Validado") ? "validado" : (registrado ? "registrado" : "pendiente");
    const detalle = h.confianza_detalle || {};
    const tituloConf = textoDetalleConfianza(detalle);

    tr.innerHTML = `
      <td><span class="patron-id-chip" style="color: #7c3aed;">${hId}</span></td>
      <td>
        <span class="patron-title">${esc(h.enunciado || h.titulo)}</span>
        <span class="patron-sub">${chipMemoria(h)} ${esc(nombreModalidad(h.modalidad))} · ${h.num_casos || 0} reclamos</span>
      </td>
      <td title="${esc(tituloConf)}">${formatoPct(h.confianza)}</td>
      <td>${chipPrioridad(h.prioridad, h.prioridad_detalle)}</td>
      <td>
        <button class="badge-estado ${estadoClase}" data-hid="${h.hipotesis_id}" title="Haga clic para validar o dictaminar">
          ${esc(estado)}
        </button>
      </td>
    `;

    const btnVal = tr.querySelector(".badge-estado");
    btnVal.addEventListener("click", (e) => {
      e.stopPropagation();
      abrirModalValidacion(h.hipotesis_id);
    });

    const idxPatron = AppState.patrones.findIndex(p => p.patron_id === h.patron_id);
    if (idxPatron >= 0) {
      tr.addEventListener("click", () =>
        abrirModalDetalle(AppState.patrones[idxPatron], "P" + String(idxPatron + 1).padStart(2, "0")));
    }
    tbody.appendChild(tr);
  });
}

function poblarSelectorValidacion(hipotesis) {
  const sel = document.getElementById("val-select-patron");
  sel.innerHTML = "";
  hipotesis.forEach((h, idx) => {
    const opt = document.createElement("option");
    opt.value = h.hipotesis_id;
    const texto = h.enunciado || h.titulo || "";
    opt.textContent = `[H${idx+1}] ${texto.substring(0, 60)}${texto.length > 60 ? "..." : ""}`;
    sel.appendChild(opt);
  });
}

// ============================================================================
// DETALLE DE UN PATRÓN (página #patron/<id>, lámina 78 pantalla 5)
// ============================================================================
// Se mantiene el nombre: todos los accesos a un patrón (tablas, gráficos, grafo, caso) pasan por aquí
function abrirModalDetalle(p) {
  cerrarEntidades();
  location.hash = "patron/" + p.patron_id;
}

function cerrarModalDetalle() {}

const etiquetaPatron = p => {
  const idx = AppState.patrones.findIndex(x => x.patron_id === p.patron_id);
  return idx >= 0 ? "P" + String(idx + 1).padStart(2, "0") : "P—";
};

// Variación frente a la corrida anterior del mismo patrón (Pattern Memory)
function tendencia(actual, anterior, formato, corrida) {
  if (anterior === null || anterior === undefined || corrida === undefined) {
    return '<span class="sin-dato">Primera detección: sin corrida anterior para comparar</span>';
  }
  const a = parseFloat(actual), b = parseFloat(anterior);
  const v = b ? Math.round((a - b) / b * 100) : 0;
  const clase = v > 0 ? "sube" : v < 0 ? "baja" : "";
  const flecha = v > 0 ? "▲ +" : v < 0 ? "▼ " : "= ";
  return `<span class="tendencia ${clase}">${flecha}${v} %</span> vs. corrida ${esc(corrida)} (${esc(formato(b))})`;
}

async function abrirPatron(patronId) {
  if (!AppState.data) { AppState.patronPendiente = patronId; return; }
  const p = AppState.patrones.find(x => x.patron_id === patronId);
  const vista = document.querySelector('.vista[data-vista="patron"]');
  if (!p) {
    document.getElementById("pp-titulo").textContent = "Patrón no vigente";
    document.getElementById("pp-sub").textContent =
      "Este patrón pertenece a una corrida anterior del Agente 2. Vuelve a la lista para ver los patrones vigentes.";
    vista.classList.add("sin-patron");
    return;
  }
  vista.classList.remove("sin-patron");
  AppState.selectedPatron = p;
  const h = (AppState.data.hipotesis || []).find(x => x.patron_id === p.patron_id) || {};
  const etq = etiquetaPatron(p);
  actualizarMigas("patron", etq);
  document.getElementById("pp-titulo").innerHTML = `Patrón ${esc(etq)} <span class="sep">·</span> ${esc(p.titulo)}`;
  document.getElementById("pp-sub").textContent = "Detalle del patrón detectado y revisión para validación analítica";
  document.getElementById("pp-chips").innerHTML = `${chipEstadoPatron(p)} ${chipPrioridad(p.prioridad, p.prioridad_detalle)}
    <span class="pill pill-azul">${esc(nombreModalidad(p.modalidad_asociada) || "—")}</span>`;

  // Hipótesis destacada dentro del cuadro de Evidencia del patrón
  const hipoEl = document.getElementById("pp-hipotesis-texto");
  if (hipoEl) {
    hipoEl.textContent = h.enunciado || p.descripcion || "Sin hipótesis especificada para este patrón.";
  }

  // Evidencia: cifras actuales; la tendencia llega con /api/patron
  document.getElementById("pp-casos").textContent = p.num_casos || 0;
  document.getElementById("pp-impacto").textContent = formatoSoles(p.impacto_total);
  document.getElementById("pp-conf").textContent = formatoPct(p.confianza);
  document.getElementById("pp-conf").title = textoDetalleConfianza(p.confianza_detalle || {});
  ["pp-casos-sub", "pp-impacto-sub", "pp-conf-sub"].forEach(id => document.getElementById(id).textContent = "…");

  // Síntesis de negocio: qué agrupa, qué lo hace inusual y qué recomendamos
  const razones = razonesDelPatron(p);
  const items = [
    ["doc", p.descripcion || "Sin descripción."],
    ["barras", razones.slice(0, 2).join(" ") || "Sin evidencia adicional."],
    ["escudo", p.accion_recomendada ? `Recomendación: ${p.accion_recomendada}` : "Requiere validación analítica del equipo de prevención."],
  ];
  document.getElementById("pp-sintesis").innerHTML = items.map(([ico, texto]) => `
    <div class="sintesis-item"><span class="sintesis-ico">${icono(ico, 20)}</span><p>${esc(texto)}</p></div>`).join("");

  renderizarVariables(p);
  renderizarCasosDelPatron(p, false);
  renderizarMemoriaDelPatron(p);
  document.getElementById("pp-porque").innerHTML =
    razones.map(r => `<li>${esc(r)}</li>`).join("") || "<li>Sin evidencia adicional.</li>";
  document.getElementById("pp-evid").textContent = p.evidencia || "Evidencia cuantitativa no disponible.";
  document.getElementById("pp-dist").textContent = p.distribuciones || "Distribución P25-P75 no calculada.";
  document.getElementById("pp-brechas").textContent = p.analisis_reglas || "Sin brechas detectadas.";
  document.getElementById("pp-regla").textContent = p.regla_propuesta || "—";
  document.querySelector('.vista[data-vista="patron"] .detalle-tecnico').open = false;
  document.getElementById("pp-exportar").href = `/api/export?tipo=casos&patron_id=${p.patron_id}`;

  // Validación
  document.getElementById("pp-estado-actual").innerHTML = `Estado actual: <strong>${esc(h.estado || "Pendiente revisión")}</strong>` +
    (h.analista && h.estado !== "Pendiente revisión" ? ` · ${esc(h.analista)}` : "");
  document.getElementById("pp-comentario").value = "";
  document.getElementById("pp-val-error").hidden = document.getElementById("pp-val-ok").hidden = true;
  document.getElementById("pp-historial").innerHTML = '<p class="texto-tenue">Cargando…</p>';

  try {
    const res = await fetch(`/api/patron?patron_id=${p.patron_id}`);
    const d = await res.json();
    if (!res.ok) throw new Error(d.error || "Error HTTP " + res.status);
    if (location.hash !== "#patron/" + p.patron_id) return;
    const ant = d.anterior || {};
    document.getElementById("pp-casos-sub").innerHTML = tendencia(p.num_casos, ant.num_casos, v => `${v} casos`, ant.corrida_id);
    document.getElementById("pp-impacto-sub").innerHTML = tendencia(p.impacto_total, ant.impacto_monto, formatoSoles, ant.corrida_id);
    document.getElementById("pp-conf-sub").innerHTML = tendencia(p.confianza, ant.confianza, v => formatoPct(v), ant.corrida_id);
    document.getElementById("pp-historial").innerHTML = d.revisiones.length
      ? d.revisiones.map(r => `<div class="revision-item"><div class="revision-cabecera"><strong>${esc(r.estado)}</strong>
          <span class="texto-tenue">${esc(r.analista || "—")} · ${esc(fechaCorta(r.created_at))} · corrida ${esc(r.corrida_id)}</span></div>
          ${r.comentario ? `<p class="m-sec-text">${esc(r.comentario)}</p>` : ""}</div>`).join("")
      : '<p class="m-sec-text texto-tenue">Aún no hay decisiones del analista sobre este patrón.</p>';
  } catch (err) {
    document.getElementById("pp-historial").innerHTML = `<p class="rev-error">No se pudo cargar el historial: ${esc(err.message)}</p>`;
  }
}

async function guardarDecisionPatron(decision) {
  const p = AppState.selectedPatron;
  const h = (AppState.data.hipotesis || []).find(x => x.patron_id === p.patron_id);
  const comentario = document.getElementById("pp-comentario").value.trim();
  const error = document.getElementById("pp-val-error"), ok = document.getElementById("pp-val-ok");
  error.hidden = ok.hidden = true;
  if (!h) { error.textContent = "Este patrón no tiene hipótesis asociada."; error.hidden = false; return; }
  if (decision === "Comentario" && !comentario) { error.textContent = "Escribe un comentario."; error.hidden = false; return; }
  // Comentar no cambia la decisión: se registra con el estado actual
  const estado = decision === "Comentario" ? (h.estado || "Pendiente revisión") : decision;
  try {
    const res = await fetch("/api/validar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hipotesis_id: h.hipotesis_id, estado, comentario }),
    });
    const r = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(r.error || "Error HTTP " + res.status);
    await cargarDatosEnVivo();
    await abrirPatron(p.patron_id);
    ok.textContent = decision === "Comentario" ? "Comentario registrado." : `Decisión registrada: ${estado}.`;
    ok.hidden = false;
  } catch (err) {
    error.textContent = err.message;
    error.hidden = false;
  }
}

// ============================================================================
// MODAL DE VALIDACIÓN HUMANA
// ============================================================================
function abrirModalValidacion(hipotesisId) {
  if (hipotesisId) {
    document.getElementById("val-select-patron").value = hipotesisId;
  }
  document.getElementById("modal-validar").classList.add("open");
}

function cerrarModalValidacion() {
  document.getElementById("modal-validar").classList.remove("open");
}

async function guardarValidacion() {
  const hipotesisId = document.getElementById("val-select-patron").value;
  const estado = document.querySelector('input[name="val_estado"]:checked').value;
  const comentario = document.getElementById("val-comentario").value;

  if (!hipotesisId) {
    console.error("No hay hipótesis seleccionada para validar.");
    return;
  }

  try {
    const res = await fetch("/api/validar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        hipotesis_id: hipotesisId,
        estado: estado,
        comentario: comentario
      })
    });
    if (!res.ok) {
      const detalle = await res.json().catch(() => ({}));
      throw new Error(detalle.error || "Error HTTP " + res.status);
    }
    document.getElementById("val-comentario").value = "";
    cerrarModalValidacion();
    await cargarDatosEnVivo();
  } catch (err) {
    console.error("Error al guardar validación:", err);
  }
}

// ============================================================================
// EVENTOS UI
// ============================================================================
function configurarEventosUI() {
  document.getElementById("btn-reload").addEventListener("click", cargarDatosEnVivo);
  document.querySelectorAll(".val-botones [data-decision]").forEach(b =>
    b.addEventListener("click", () => guardarDecisionPatron(b.dataset.decision)));
  document.getElementById("pp-ver-todos").addEventListener("click", () => {
    if (AppState.selectedPatron) renderizarCasosDelPatron(AppState.selectedPatron, !AppState.verTodosCasos);
  });

  document.getElementById("btn-open-global-val").addEventListener("click", () => abrirModalValidacion());
  document.getElementById("btn-close-val-modal").addEventListener("click", cerrarModalValidacion);
  document.getElementById("btn-cancel-val").addEventListener("click", cerrarModalValidacion);
  document.getElementById("btn-save-val").addEventListener("click", guardarValidacion);

  document.getElementById("btn-close-entidades").addEventListener("click", cerrarEntidades);
  document.getElementById("btn-close-entidades-footer").addEventListener("click", cerrarEntidades);
  document.querySelectorAll('.vista[data-vista="caso"] [data-accion]').forEach(b =>
    b.addEventListener("click", () => {
      document.getElementById("menu-acciones").hidden = true;
      abrirRevision(b.dataset.accion);
    }));
  document.getElementById("btn-acciones").addEventListener("click", e => {
    e.stopPropagation();
    const menu = document.getElementById("menu-acciones");
    menu.hidden = !menu.hidden;
  });
  document.addEventListener("click", () => { document.getElementById("menu-acciones").hidden = true; });
  document.getElementById("cp-nota-guardar").addEventListener("click", guardarNota);
  // "ver más" de textos largos y enlaces que llevan a otra pestaña del caso
  document.querySelector('.vista[data-vista="caso"]').addEventListener("click", e => {
    const ver = e.target.closest("[data-ver-mas]");
    if (ver) {
      const fila = ver.parentElement;
      const abierto = !fila.querySelector(".completo").hidden;
      fila.querySelector(".completo").hidden = abierto;
      fila.querySelector(".resumido").hidden = !abierto;
      ver.textContent = abierto ? "ver más" : "ver menos";
    }
    const tab = e.target.closest("[data-ir-tab]");
    if (tab) mostrarTabCaso(tab.dataset.irTab);
  });
  document.querySelectorAll(".caso-tab").forEach(b => b.addEventListener("click", () => mostrarTabCaso(b.dataset.tab)));
  document.getElementById("btn-close-rev").addEventListener("click", cerrarRevision);
  document.getElementById("btn-cancel-rev").addEventListener("click", cerrarRevision);
  document.getElementById("btn-save-rev").addEventListener("click", guardarRevision);
  document.getElementById("busqueda-global").addEventListener("keydown", e => {
    if (e.key === "Enter") buscarGlobal(e.target.value);
  });
  document.getElementById("casos-buscar").addEventListener("input", renderizarTablaCasos);
  document.getElementById("casos-filtro-modalidad").addEventListener("change", renderizarTablaCasos);
  ["casos-filtro-riesgo", "casos-filtro-variante", "casos-desde", "casos-hasta"].forEach(id =>
    document.getElementById(id).addEventListener("change", renderizarTablaCasos));
  document.querySelectorAll("#filtro-sintesis [data-sintesis]").forEach(b => b.addEventListener("click", () => {
    AppState.filtroSintesis = b.dataset.sintesis || null;
    document.querySelectorAll("#filtro-sintesis [data-sintesis]").forEach(x => x.classList.toggle("activo", x === b));
    if (AppState.data) {
      renderizarTablaPatrones(AppState.data.patrones || []);
      renderizarTablaHipotesis(AppState.data.hipotesis || []);
    }
  }));
  document.getElementById("periodo-casos").addEventListener("change", renderizarCasosPorMes);
  ["g-periodo", "g-tipologia", "g-patron"].forEach(id =>
    document.getElementById(id).addEventListener("change", () => { AppState.grafoCentro = null; renderizarGrafo(); }));
  document.getElementById("cfg-guardar").addEventListener("click", guardarConfiguracion);
  document.getElementById("casos-filtro-mes").addEventListener("click", e => {
    if (e.target.tagName === "BUTTON") { AppState.filtroMes = null; renderizarTablaCasos(); }
  });

  // Barra lateral: cada botón muestra su vista
  document.querySelectorAll(".nav-item[data-vista]").forEach(btn =>
    btn.addEventListener("click", () => mostrarVista(btn.dataset.vista)));
  window.addEventListener("hashchange", () => mostrarVista(location.hash.slice(1), false));
  mostrarVista(location.hash.slice(1) || "resumen", false);

  // Cerrar modales con clic fuera
  window.addEventListener("click", (e) => {
    const mValidar = document.getElementById("modal-validar");
    if (e.target === mValidar) cerrarModalValidacion();
    if (e.target === document.getElementById("modal-revision")) cerrarRevision();
    if (e.target === document.getElementById("modal-entidades")) cerrarEntidades();
  });
}

// ============================================================================
// RECORRIDO DE LOS DATOS (EMBUDO)
// ============================================================================
function renderizarEmbudo(embudo) {
  const cont = document.getElementById("embudo");
  if (!embudo) { cont.innerHTML = ""; return; }

  const rev = embudo.revision || {};
  const validadas = rev["Validado"] || 0;
  const investigacion = rev["En Investigación"] || 0;
  const descartadas = rev["Descartado"] || 0;
  const precision = embudo.precision_a1 === null || embudo.precision_a1 === undefined
    ? "" : `${String(embudo.precision_a1).replace(".", ",")} % de acierto`;

  const pasos = [
    { agente: "fuente", tag: "Fuentes", valor: embudo.llamadas, etiqueta: "Llamadas de reclamo",
      sub: "+ transacciones, clientes y tickets" },
    { agente: "a1", tag: "Agente 1", valor: embudo.casos_analizados, etiqueta: "Reclamos analizados", sub: precision,
      vista: "reclamos", ayuda: "Ver los reclamos" },
    { agente: "a2", tag: "Agente 2", valor: embudo.clusters ?? "—", etiqueta: "Grupos de casos parecidos",
      sub: "misma forma de operar" },
    { agente: "a2", tag: "Agente 2", valor: embudo.entidades_riesgo ?? 0, etiqueta: "Comercios sospechosos",
      sub: (embudo.entidades_detalle || []).map(e => e.entidad_id).join(" · ") || "concentran víctimas o cobros",
      accion: "entidades", ayuda: "Clic para ver el detalle" },
    { agente: "a2", tag: "Agente 2", valor: embudo.patrones, etiqueta: "Patrones de fraude", vista: "patrones", ayuda: "Ver los patrones",
      sub: textoClasificacion(embudo.clasificacion) },
    { agente: "humano", tag: "Analista", valor: `${validadas}/${embudo.patrones}`, etiqueta: "Hipótesis validadas",
      vista: "patrones", ayuda: "Validar hipótesis",
      sub: `${investigacion} en investigación · ${descartadas} descartadas · ` +
           `${(embudo.clasificacion || {})["Nuevo"] || 0} nuevos requieren decisión` },
  ];

  cont.innerHTML = pasos.map((p, i) => `
    ${i > 0 ? '<div class="embudo-flecha">→</div>' : ""}
    <div class="embudo-paso${p.accion || p.vista ? " clicable" : ""}" ${p.accion ? `data-accion="${p.accion}"` : ""} ${p.vista ? `data-ir="${p.vista}"` : ""} ${p.ayuda ? `title="${esc(p.ayuda)}"` : ""}>
      <span class="embudo-agente ${p.agente}">${esc(p.tag)}</span>
      <span class="embudo-valor">${esc(p.valor)}</span>
      <span class="embudo-etiqueta">${esc(p.etiqueta)}</span>
      <span class="embudo-sub">${esc(p.sub)}</span>
      ${p.ayuda ? `<span class="embudo-ayuda">${esc(p.ayuda)} →</span>` : ""}
    </div>`).join("");
  cont.querySelectorAll("[data-ir]").forEach(el => el.addEventListener("click", () => mostrarVista(el.dataset.ir)));
  cont.querySelectorAll('[data-accion="entidades"]').forEach(el =>
    el.addEventListener("click", () => abrirEntidades(embudo.entidades_detalle || [])));

  const omitidos = embudo.omitidos_memoria || [];
  document.getElementById("embudo-memoria").textContent =
    `Memoria del sistema: recuerda ${embudo.memoria_total ?? 0} patrones; ${embudo.memoria_evaluados ?? 0} ya tienen decisión del analista` +
    (omitidos.length ? ` · ${omitidos.length} descartado(s) no se volvieron a proponer porque no hay evidencia nueva.` : ".");

  document.getElementById("embudo-corrida").textContent =
    embudo.corrida_id ? `Corrida ${embudo.corrida_id}` : "Sin corridas";
}

// ============================================================================
// CASOS ANALIZADOS POR EL AGENTE 1
// ============================================================================
function formatoSoles(monto) {
  if (monto === null || monto === undefined) return "—";
  return "S/ " + parseFloat(monto).toLocaleString("es-PE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// Los casos analizados antes de v3.1 solo tienen etiqueta (alta/media/baja), sin puntaje numérico
function textoConfianza(caso, detallado = false) {
  if (caso.confianza_score !== null && caso.confianza_score !== undefined) {
    return `${Math.round(parseFloat(caso.confianza_score))}/100`;
  }
  if (!caso.confianza) return "—";
  const etiqueta = caso.confianza.charAt(0).toUpperCase() + caso.confianza.slice(1);
  return detallado ? `${etiqueta} (sin puntaje numérico)` : etiqueta;
}

function celdaAcierto(caso) {
  if (caso.correcta === true) return '<span class="acierto si" title="Coincide con la modalidad real">✓</span>';
  if (caso.correcta === false) return '<span class="acierto no" title="No coincide con la modalidad real">✗</span>';
  return "";
}

async function cargarCasos() {
  try {
    const res = await fetch("/api/casos");
    if (!res.ok) throw new Error("Error HTTP " + res.status);
    AppState.casos = await res.json();
    AppState.casosPorId = {};
    AppState.casos.forEach(c => AppState.casosPorId[c.call_id] = c);

    const sel = document.getElementById("casos-filtro-modalidad");
    const actual = sel.value;
    const modalidades = [...new Set(AppState.casos.map(c => c.modalidad).filter(Boolean))].sort();
    sel.innerHTML = '<option value="">Todos los tipos de fraude</option>' +
      modalidades.map(m => `<option value="${esc(m)}">${esc(nombreModalidad(m))}</option>`).join("");
    sel.value = modalidades.includes(actual) ? actual : "";

    renderizarTablaCasos();
  } catch (err) {
    console.error("Error al cargar casos:", err);
  }
}

function renderizarTablaCasos() {
  const texto = document.getElementById("casos-buscar").value.trim().toLowerCase();
  const modalidad = document.getElementById("casos-filtro-modalidad").value;
  const riesgo = document.getElementById("casos-filtro-riesgo").value;
  const variante = parseInt(document.getElementById("casos-filtro-variante").value, 10);
  const desde = document.getElementById("casos-desde").value;
  const hasta = document.getElementById("casos-hasta").value;
  const mes = AppState.filtroMes;
  const filtroMes = document.getElementById("casos-filtro-mes");
  filtroMes.hidden = !mes;
  if (mes) { const ms = mes.split(","); filtroMes.innerHTML = `${ms.length > 1 ? `De ${esc(ms[0])} a ${esc(ms[ms.length - 1])}` : `Mes ${esc(mes)}`} <button title="Quitar filtro">×</button>`; }
  const filtrados = AppState.casos.filter(c =>
    (!mes || mes.split(",").some(x => String(c.fecha_llamada || "").startsWith(x))) &&
    (!modalidad || c.modalidad === modalidad) &&
    (!riesgo || c.nivel_riesgo === riesgo) &&
    (!variante || c.variante === variante) &&
    (!desde || String(c.fecha_llamada || "").slice(0, 10) >= desde) &&
    (!hasta || String(c.fecha_llamada || "").slice(0, 10) <= hasta) &&
    (!texto || [c.call_id, codigoCaso(c.call_id), c.modalidad, nombreModalidad(c.modalidad), c.merchant_category,
                c.ciudad_cliente, c.ciudad_comercio, c.terminal_id, c.canal_txn, c.variante_nombre, c.estado_revision]
      .some(v => String(v ?? "").toLowerCase().includes(texto)))
  );

  const correctas = AppState.casos.filter(c => c.correcta === true).length;
  document.getElementById("casos-badge").textContent =
    `${filtrados.length} de ${AppState.casos.length} · ${correctas} acertados`;

  const tbody = document.getElementById("tbody-casos");
  tbody.innerHTML = "";
  filtrados.forEach(c => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><span class="patron-id-chip">${codigoCaso(c.call_id)}</span></td>
      <td>${celdaAcierto(c)}${esc(nombreModalidad(c.modalidad))}</td>
      <td class="texto-tenue">${esc(nombreModalidad(c.modalidad_real) || "—")}</td>
      <td>${esc(textoConfianza(c))}</td>
      <td>${chipRiesgo(c)}</td>
      <td>${chipVariante(c)}</td>
      <td>${chipRevision(c.estado_revision)}</td>
      <td>${esc(formatoSoles(c.amount))}</td>
      <td>${esc(c.merchant_category || "—")}</td>
      <td>${esc(c.canal_txn || "—")} <span class="texto-tenue">/ ${esc(c.modo_entrada || "—")}</span></td>
    `;
    tr.addEventListener("click", () => irACaso(c.call_id));
    tbody.appendChild(tr);
  });
}

// ============================================================================
// DE UN PATRÓN A SUS CASOS
// ============================================================================
function renderizarCasosDelPatron(p, todos) {
  AppState.verTodosCasos = todos;
  const ids = p.casos_asociados || [];
  const mostrar = todos ? ids : ids.slice(0, 5);
  const boton = document.getElementById("pp-ver-todos");
  boton.textContent = ids.length > 5 ? (todos ? "Ver menos" : `Ver los ${ids.length} casos`) : "";
  boton.hidden = ids.length <= 5;
  const tbody = document.getElementById("pp-casos-tabla");
  tbody.innerHTML = "";
  mostrar.forEach(id => {
    const c = AppState.casosPorId[id] || { call_id: id };
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><span class="patron-id-chip">${codigoCaso(id)}</span></td>
      <td>${esc(fechaDMY(c.fecha_llamada))}</td>
      <td class="nowrap">${esc(formatoSoles(c.amount))}</td>
      <td>${esc(c.ciudad_comercio || "—")}</td>
      <td>${chipRevision(c.estado_revision)}</td>`;
    tr.addEventListener("click", () => irACaso(id));
    tbody.appendChild(tr);
  });
}

// ============================================================================
// VISOR DE CASO: LO QUE RECIBIÓ LA IA Y LO QUE PRODUJO
// ============================================================================
function filasKV(filas) {
  return filas.map(([clave, valor, noEnviado]) => `
    <span class="kv-clave">${esc(clave)}</span>
    <span class="kv-valor">${esc(valor ?? "—")}${noEnviado ? `<span class="no-enviado">${esc(noEnviado)}</span>` : ""}</span>
  `).join("");
}

function fechaCorta(valor) {
  return valor ? String(valor).replace("T", " ").slice(0, 16) : "—";
}

const codigoCaso = id => "C-" + String(id).padStart(4, "0");

function irACaso(callId) {
  cerrarModalDetalle();
  cerrarEntidades();
  location.hash = "caso/" + callId;
}

// Variante de la ficha (lámina 16 de la propuesta) y estado de revisión del analista
function chipVariante(c) {
  if (!c.variante) return "—";
  const clase = { 1: "v1", 2: "v2", 3: "v3", 4: "v4", 5: "v5" }[c.variante];
  return `<span class="chip-variante ${clase}" title="${esc(c.variante_accion || "")}">${c.variante} · ${esc(c.variante_nombre)}</span>`;
}

function chipRevision(estado) {
  const e = estado || "Pendiente";
  const clase = { "Pendiente": "pendiente", "Confirmada": "confirmada", "Corregida": "corregida",
                  "Nueva modalidad": "nueva" }[e] || "pendiente";
  return `<span class="chip-revision ${clase}">${esc(e)}</span>`;
}

// Íconos (trazo simple) para filas y línea de tiempo
const SVG = {
  tarjeta: '<rect x="2" y="5" width="20" height="14" rx="2"/><line x1="2" y1="10" x2="22" y2="10"/>',
  alerta: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
  base: '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14c0 1.7 4 3 9 3s9-1.3 9-3V5"/><path d="M3 12c0 1.7 4 3 9 3s9-1.3 9-3"/>',
  doc: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>',
  persona: '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
  idea: '<path d="M9 18h6"/><path d="M10 22h4"/><path d="M12 2a7 7 0 0 0-4 12.7V17h8v-2.3A7 7 0 0 0 12 2z"/>',
  escudo: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><polyline points="9 12 11 14 15 10"/>',
  etiqueta: '<path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L2 12V2h10l8.6 8.6a2 2 0 0 1 0 2.8z"/><line x1="7" y1="7" x2="7.01" y2="7"/>',
  barras: '<line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>',
  ubicacion: '<path d="M21 10c0 7-9 13-9 13S3 17 3 10a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/>',
  tienda: '<path d="M3 9l1.5-5h15L21 9"/><path d="M4 9v11h16V9"/><path d="M3 9h18"/><path d="M9 20v-6h6v6"/>',
  globo: '<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15 15 0 0 1 0 20a15 15 0 0 1 0-20z"/>',
  categoria: '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>',
  pantalla: '<rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>',
  carrito: '<circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.7 13.4a2 2 0 0 0 2 1.6h9.7a2 2 0 0 0 2-1.6L23 6H6"/>',
  telefono: '<path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L8 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.7 2z"/>',
  chip: '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/>',
  red: '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.6" y1="13.5" x2="15.4" y2="17.5"/><line x1="15.4" y1="6.5" x2="8.6" y2="10.5"/>',
  check: '<polyline points="20 6 9 17 4 12"/>',
  ticket: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
};
const icono = (n, t = 18) => `<svg width="${t}" height="${t}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${SVG[n] || ""}</svg>`;

const EVENTO = {
  compromiso: ["alerta", "rojo"], transaccion: ["carrito", "azul-fuerte"], ticket: ["ticket", "gris"],
  llamada: ["telefono", "celeste"], ia: ["chip", "lila"], patron: ["red", "lila"], analista: ["check", "verde"],
};

const fechaDMY = v => { if (!v) return "—"; const [a, m, d] = String(v).slice(0, 10).split("-"); return `${d}/${m}/${a}`; };
const horaHM = v => (v && String(v).length > 10) ? String(v).replace("T", " ").slice(11, 16) : "";

// Texto largo de la IA: muestra la primera frase y permite ver el resto
function textoResumido(texto, max = 150) {
  const t = String(texto || "").trim();
  if (!t) return '<span class="texto-tenue">— (no generado en esta versión)</span>';
  const corte = t.search(/(?<=[.;])\s|\s(?=\d\)\s)/);
  let corto = corte > 25 && corte < max ? t.slice(0, corte) : t.slice(0, max);
  if (corto.length >= t.length) return esc(t);
  return `<span class="resumido">${esc(corto.replace(/[.;,\s]+$/, ""))}…</span><span class="completo" hidden>${esc(t)}</span>
    <button class="link-ver" data-ver-mas>ver más</button>`;
}

function filaInfo(ico, etiqueta, valorHtml, enlace) {
  return `<div class="info-fila"><span class="info-ico">${icono(ico)}</span><span class="info-etq">${esc(etiqueta)}</span>
    <span class="info-val">${valorHtml}</span>${enlace ? `<span class="info-link">${enlace}</span>` : ""}</div>`;
}
// Campo que la maqueta pide pero que nuestra base de datos no tiene: se muestra vacío, sin inventar
const sinDato = () => '<span class="sin-dato" title="Este dato no existe en la base de datos del prototipo (datos sintéticos)">No disponible en la base de datos</span>';
const enlaceTab = (tab, texto) => `<button class="link-ver" data-ir-tab="${tab}">${esc(texto)}</button>`;

const COLORES_TAG = ["azul", "lila", "ambar", "verde", "rosa", "turquesa"];

function renderizarCaso({ caso, tickets = [], patrones = [], entradas_ia = {}, revisiones = [], transacciones = [],
                          cliente = { perfil: {}, cuentas: [], tarjetas: [] }, linea_tiempo = [] }) {
  const modalidadIA = caso.ai_tipificacion || caso.modalidad_predicha;
  const modalidad = caso.tipologia_analista || modalidadIA;
  const marca = entradas_ia.contexto_transaccional_ampliado ? null : "no enviado a la IA";
  const sospechosos = {};
  ((AppState.data && AppState.data.embudo && AppState.data.embudo.entidades_detalle) || []).forEach(e => sospechosos[e.entidad_id] = e);

  // Cabecera: "Caso C-0058 · 06/09/2026 · Llamada" + chips
  document.getElementById("cp-titulo").innerHTML =
    `Caso ${codigoCaso(caso.call_id)} <span class="sep">·</span> ${fechaDMY(caso.fecha_llamada)} <span class="sep">·</span> Llamada`;
  const nivel = (caso.nivel_riesgo || "").toLowerCase();
  document.getElementById("cp-chips").innerHTML = `
    <span class="pill pill-azul">${icono("tarjeta", 16)} ${esc(nombreModalidad(modalidad) || "Sin tipología")}</span>
    ${caso.nivel_riesgo ? `<span class="pill pill-riesgo ${nivel}" title="${esc((caso.riesgo_razones || []).join(" · "))}">${icono("alerta", 16)} Riesgo ${esc(nivel)}</span>` : ""}
    ${caso.variante ? `<span class="pill pill-variante v${caso.variante}" title="${esc(caso.variante_accion || "")}">Ficha ${caso.variante} · ${esc(caso.variante_nombre)}</span>` : ""}
    ${chipRevision(caso.estado_revision)}`;
  document.getElementById("cp-meta").textContent = caso.variante_accion ? `Qué hace el analista: ${caso.variante_accion}` : "";
  actualizarMigas("caso", caso.call_id);

  // Información del caso
  const estadoTxt = caso.estado_revision === "Nueva modalidad" || caso.ai_estado_tipificacion === "Nueva"
    ? "Candidato a nueva modalidad"
    : caso.ai_estado_tipificacion === "Existente" ? "Modalidad existente en el catálogo"
    : "Tipología del catálogo (analizado con una versión anterior del Agente 1)";
  const versionManual = caso.version_manual
    ? `Manual v${esc(caso.version_manual)}`
    : `<span class="sin-dato" title="La ficha se generó antes de que se registrara la versión del Manual en cada caso">versión del Manual no registrada</span>`;
  const correccion = caso.tipologia_analista && caso.tipologia_analista !== modalidadIA
    ? ` <span class="texto-tenue">· la IA dijo ${esc(nombreModalidad(modalidadIA))}</span>` : "";
  const score = caso.confianza_score !== null && caso.confianza_score !== undefined ? Math.round(caso.confianza_score) : null;
  const tags = [...new Set([caso.ai_tipificacion_tags, caso.ai_insights_tags, caso.ai_resumen_caso_tags, caso.ai_blindaje_tags]
    .filter(Boolean).join(",").split(/[,;]/).map(t => t.trim().replace(/_/g, " ")).filter(Boolean))].slice(0, 6);
  const minutos = Math.max(1, Math.round((caso.duracion_segundos || 0) / 60));
  document.getElementById("cp-info").innerHTML = [
    filaInfo("base", "Transacción", `${esc(formatoSoles(caso.amount))} <span class="sep">·</span> ${esc(caso.merchant_category || "—")}
      <span class="sep">·</span> ${esc(caso.canal_txn || "—")} <span class="sep">·</span> ${esc(horaHM(caso.txn_date) || "—")} h`),
    filaInfo("doc", "Estado", `${estadoTxt} <span class="sep">·</span> ${versionManual} ${chipEstadoFicha(caso)}`),
    filaInfo("tarjeta", "Tipología", `<strong>${esc(nombreModalidad(modalidad) || "—")}</strong>${correccion}`),
    filaInfo("persona", "Resumen", textoResumido(caso.ai_resumen_caso)),
    filaInfo("idea", "Insight", textoResumido(caso.ai_insights)),
    filaInfo("escudo", "Blindaje sugerido", textoResumido(caso.ai_blindaje, 90)),
    ...(caso.ai_situacion && caso.ai_situacion !== "fraude" ? [filaInfo("alerta",
      caso.ai_situacion === "no_fraude" ? "Por qué no es fraude" : "Qué no se entiende",
      esc(caso.ai_motivo_situacion || "—"))] : []),
    filaInfo("doc", "Evidencia", `${(caso.ai_evidencia || []).length
        ? (caso.ai_evidencia || []).map(e => `<span class="cita">«${esc(e.cita)}»</span>
            ${e.sustenta ? `<span class="cita-sustenta">${esc(e.sustenta)}</span>` : ""}`).join("")
        : `Transcripción de la llamada (${minutos} min) · ${esc(caso.canal_reclamo || "—")}
           <span class="sin-dato">· esta ficha no tiene frases citadas (versión anterior del Agente 1)</span>`}
      <button class="link-ver con-ico" data-ir-tab="llamada">${icono("doc", 15)} Ver transcripción</button>`),
    filaInfo("etiqueta", "Tags", tags.map((t, k) => `<span class="tag-color ${COLORES_TAG[k % COLORES_TAG.length]}">${esc(t)}</span>`).join("")
      || '<span class="texto-tenue">—</span>'),
    filaInfo("barras", "Confianza del agente", score !== null
      ? `<span class="barra-conf grande"><span style="width:${score}%"></span></span><strong class="conf-num">${score} %</strong>`
      : esc(textoConfianza(caso, true))),
    filaInfo("check", "Modalidad real (prueba)", `${celdaAcierto(caso)}${esc(nombreModalidad(caso.modalidad_real) || "—")}
      <span class="texto-tenue">· solo para medir el acierto, la IA no la ve</span>`),
  ].join("");

  // Datos del cliente y del comercio
  const p = cliente.perfil || {};
  document.getElementById("cp-datos-cliente").innerHTML = [
    filaInfo("persona", "Cliente", `ID ${esc(String(caso.customer_id).padStart(5, "0"))}`, enlaceTab("cliente", "Ver perfil")),
    filaInfo("tarjeta", "Tarjeta", `<span class="nowrap">**** ${esc(String(caso.card_id ?? "").padStart(4, "0"))}</span> <span class="texto-tenue">${esc(caso.card_type || "")}</span>`,
      enlaceTab("transacciones", "Ver movimientos")),
    filaInfo("persona", "Titular", sinDato()),
    filaInfo("categoria", "Segmento", sinDato()),
    filaInfo("doc", "Ocupación", esc(p.occupation || caso.occupation || "—")),
    filaInfo("base", "Límite de crédito", esc(formatoSoles(caso.credit_limit))),
    filaInfo("ubicacion", "Ciudad habitual", esc(p.city || caso.ciudad_cliente || "—")),
  ].join("");
  const sosp = sospechosos[caso.terminal_id];
  document.getElementById("cp-datos-comercio").innerHTML = [
    filaInfo("tienda", "Comercio", `<span class="texto-mono">${esc(caso.terminal_id || "—")}</span>
      ${sosp ? '<span class="pill-mini rojo">sospechoso</span>' : ""}`),
    filaInfo("tienda", "Nombre comercial", sinDato()),
    filaInfo("globo", "País", sinDato()),
    filaInfo("ubicacion", "Ciudad", esc(caso.ciudad_comercio || "—")),
    filaInfo("categoria", "MCC", sinDato()),
    filaInfo("categoria", "Rubro", esc(caso.merchant_category || "—")),
    filaInfo("pantalla", "Canal", `${esc(caso.canal_txn || "—")} <span class="sep">·</span> ${esc(caso.modo_entrada || "—")}`,
      enlaceTab("comercio", "Ver más detalles")),
  ].join("");

  // Línea de tiempo
  document.getElementById("cp-linea").innerHTML = linea_tiempo.map((e, k) => {
    const [ico, color] = EVENTO[e.tipo] || ["doc", "gris"];
    const detalle = e.tipo === "ia" ? nombreModalidad(e.detalle) : e.detalle;
    return `${k ? '<span class="lt-flecha">→</span>' : ""}
      <div class="lt-evento" title="${esc(e.detalle)}">
        <span class="lt-icono ${color}">${icono(ico, 22)}</span>
        <span class="lt-hora">${esc(horaHM(e.fecha) || fechaDMY(e.fecha))}</span>
        <span class="lt-fecha">${horaHM(e.fecha) ? esc(fechaDMY(e.fecha)) : ""}</span>
        <span class="lt-titulo">${esc(e.titulo)}</span>
        <span class="lt-detalle">${esc(detalle.length > 60 ? detalle.slice(0, 58) + "…" : detalle)}</span>
      </div>`;
  }).join("") || '<p class="texto-tenue">Sin eventos.</p>';

  // Transacciones
  document.getElementById("cp-tx-nota").textContent =
    `Tarjeta ${caso.card_id ?? "—"} · ${transacciones.length} movimientos hasta 30 días después del fraude`;
  document.getElementById("cp-tx").innerHTML = transacciones.map(t => {
    const marcas = [t.es_reclamada ? '<span class="chip-revision corregida">Reclamada</span>' : "",
                    t.is_fraud && !t.es_reclamada ? '<span class="chip-revision nueva">Fraude</span>' : "",
                    sospechosos[t.terminal_id] ? '<span class="chip-variante v4">Comercio sospechoso</span>' : ""].join(" ");
    return `<tr class="${t.es_reclamada ? "fila-reclamada" : ""}">
      <td>${esc(fechaCorta(t.txn_date))}</td><td>${esc(t.merchant_category)}</td><td>${esc(formatoSoles(t.amount))}</td>
      <td>${esc(t.canal || "—")} <span class="texto-tenue">/ ${esc(t.modo_entrada || "—")}</span></td>
      <td class="texto-mono">${esc(t.terminal_id || "—")}</td><td>${esc(t.ciudad_comercio || "—")}</td><td>${marcas}</td></tr>`;
  }).join("") || '<tr><td colspan="7" class="texto-tenue">Sin movimientos.</td></tr>';

  // Cliente
  document.getElementById("cp-perfil").innerHTML = filasKV([
    ["Cliente", p.customer_id], ["Ciudad", p.city], ["Ocupación", p.occupation],
    ["Ingreso anual", formatoSoles(p.annual_income), marca], ["Score crediticio", p.credit_score, marca],
    ["Cliente desde", fechaDMY(p.join_date)],
  ]);
  document.getElementById("cp-cuentas").innerHTML = (cliente.cuentas || []).map(c =>
    `<tr><td>${esc(c.account_id)}</td><td>${esc(c.account_type)}</td><td>${esc(formatoSoles(c.balance))}</td>
     <td>${esc(c.status)}</td><td>${esc(fechaDMY(c.open_date))}</td></tr>`).join("") ||
    '<tr><td colspan="5" class="texto-tenue">Sin cuentas.</td></tr>';
  document.getElementById("cp-tarjetas").innerHTML = (cliente.tarjetas || []).map(t =>
    `<tr class="${t.card_id === caso.card_id ? "fila-reclamada" : ""}"><td>${esc(t.card_id)}${t.card_id === caso.card_id ? ' <span class="texto-tenue">(del reclamo)</span>' : ""}</td>
     <td>${esc(t.card_type)}</td><td>${esc(formatoSoles(t.credit_limit))}</td><td>${esc(t.status)}</td>
     <td>${esc(fechaDMY(t.expiry_date))}</td></tr>`).join("") ||
    '<tr><td colspan="5" class="texto-tenue">Sin tarjetas.</td></tr>';
  document.getElementById("cp-tickets").innerHTML = tickets.length
    ? tickets.map(t => `<div class="ticket-item">${esc(t.date_opened)} · ${esc(t.issue_type)} · ${esc(t.status)}</div>`).join("")
    : '<p class="m-sec-text texto-tenue">Sin tickets en los 180 días previos a la llamada.</p>';

  // Comercio
  document.getElementById("cp-comercio").innerHTML = filasKV([
    ["Terminal / comercio", caso.terminal_id], ["Rubro", caso.merchant_category], ["Ciudad", caso.ciudad_comercio, marca],
    ["Canal", caso.canal_txn, marca], ["Modo de entrada", caso.modo_entrada, marca], ["Fecha de la compra", fechaCorta(caso.txn_date)],
  ]);
  const tipoEnt = sosp ? (TIPOS_ENTIDAD[sosp.tipo] || { nombre: sosp.tipo, explica: "", accion: "" }) : null;
  document.getElementById("cp-comercio-riesgo").innerHTML = sosp
    ? `<p class="m-sec-text"><span class="pill-mini rojo">${esc(tipoEnt.nombre)}</span></p>
       <p class="m-sec-text">${esc(tipoEnt.explica)}</p>
       <p class="m-sec-text"><strong>${esc(sosp.n_victimas ?? sosp.n_casos)}</strong> víctimas, cuando por casualidad se esperaban
         <strong>${esc(String(sosp.esperado_por_azar ?? "—").replace(".", ","))}</strong>.</p>
       <p class="m-sec-text"><strong>Acción sugerida:</strong> ${esc(tipoEnt.accion)}</p>`
    : '<p class="m-sec-text texto-tenue">El Agente 2 no detectó concentración anormal de víctimas en este comercio.</p>';
  const contPatrones = document.getElementById("cp-patrones");
  contPatrones.innerHTML = patrones.length ? "" :
    '<p class="m-sec-text texto-tenue">Este caso no forma parte de ningún patrón vigente.</p>';
  patrones.forEach(pt => {
    const chip = document.createElement("span");
    chip.className = "chip-patron";
    chip.textContent = pt.titulo;
    chip.addEventListener("click", () => {
      const idx = AppState.patrones.findIndex(x => x.patron_id === pt.patron_id);
      if (idx >= 0) abrirModalDetalle(AppState.patrones[idx], "P" + String(idx + 1).padStart(2, "0"));
    });
    contPatrones.appendChild(chip);
  });

  // Llamada
  const notaVersion = document.getElementById("cp-nota-version");
  notaVersion.hidden = !!entradas_ia.contexto_exacto_guardado;
  notaVersion.textContent = entradas_ia.contexto_exacto_guardado ? "" :
    "Este caso se analizó con una versión anterior del Agente 1. La IA recibió la transcripción, el monto, " +
    "comercio y fecha de la transacción, y el tipo de tarjeta, límite, ciudad y ocupación del cliente" +
    (entradas_ia.tickets ? ", además de sus tickets de soporte." : "; no recibió los tickets de soporte.");
  document.getElementById("cp-transcript").textContent = caso.transcript || "—";
  const ocultos = Object.entries(caso.datos_enmascarados || {});
  document.getElementById("cp-traza").innerHTML = caso.corrida_a1_id
    ? `Corrida ${esc(caso.corrida_a1_id)} del Agente 1 · modelo ${esc(caso.modelo_ia || "—")} · instrucciones ${esc(caso.prompt_version || "—")} ·
       Manual v${esc(caso.version_manual || "—")} · datos ocultados antes de la IA:
       ${ocultos.length ? ocultos.map(([k, n]) => `${n} ${esc(k.toLowerCase())}`).join(", ") : "ninguno encontrado"}`
    : '<span class="sin-dato">Ficha anterior al registro por corrida: no se guardó modelo, versión de instrucciones ni versión del Manual.</span>';
  document.getElementById("cp-dialogo").textContent = caso.ai_dialogo_limpio || "—";
  document.getElementById("cp-razonamiento").innerHTML = formatearRazonamiento(caso.ai_razonamiento_oculto);
  document.getElementById("cp-blindaje").textContent = caso.ai_blindaje || "—";
  const wrap = document.getElementById("cp-contexto-wrap");
  wrap.hidden = !entradas_ia.contexto_exacto_guardado;
  wrap.open = false;
  document.getElementById("cp-contexto").textContent = caso.ai_contexto_entrada || "";

  // Historial (decisiones) y notas (comentarios)
  const decisiones = revisiones.filter(r => r.accion !== "Comentario");
  const notas = revisiones.filter(r => r.comentario);
  document.querySelector('.caso-tab[data-tab="historial"]').textContent = `Historial${decisiones.length ? ` (${decisiones.length})` : ""}`;
  document.querySelector('.caso-tab[data-tab="notas"]').textContent = `Notas${notas.length ? ` (${notas.length})` : ""}`;
  document.getElementById("cp-historial").innerHTML = decisiones.length
    ? decisiones.map(r => `
      <div class="revision-item">
        <div class="revision-cabecera"><strong>${esc(r.accion === "Comentario" ? "Nota" : r.accion)}</strong>
          <span class="texto-tenue">${esc(r.analista || "—")} · ${esc(fechaCorta(r.created_at))}</span></div>
        ${r.accion === "Corregir" ? `<p class="m-sec-text">${esc(nombreModalidad(r.tipologia_anterior))} → <strong>${esc(nombreModalidad(r.tipologia_nueva))}</strong></p>` : ""}
        ${r.comentario ? `<p class="m-sec-text">${esc(r.comentario)}</p>` : ""}
      </div>`).join("")
    : '<p class="m-sec-text texto-tenue">El analista aún no tomó una decisión sobre este caso. Usa los botones del Detalle o el menú Acciones.</p>';
  document.getElementById("cp-notas").innerHTML = notas.length
    ? notas.map(r => `<div class="revision-item"><div class="revision-cabecera"><strong>${esc(r.analista || "—")}</strong>
        <span class="texto-tenue">${esc(fechaCorta(r.created_at))}${r.accion !== "Comentario" ? " · al " + esc(r.accion.toLowerCase()) : ""}</span></div>
        <p class="m-sec-text">${esc(r.comentario)}</p></div>`).join("")
    : '<p class="m-sec-text texto-tenue">Sin notas todavía.</p>';
}

async function abrirCaso(callId) {
  AppState.casoActual = null;
  const vista = document.querySelector('.vista[data-vista="caso"]');
  document.getElementById("cp-titulo").textContent = "Caso " + codigoCaso(callId);
  document.getElementById("cp-chips").innerHTML = "";
  document.getElementById("cp-meta").textContent = "Cargando…";
  vista.classList.add("cargando");
  try {
    const res = await fetch(`/api/caso?call_id=${encodeURIComponent(callId)}`);
    const datos = await res.json();
    if (!res.ok) throw new Error(datos.error || "Error HTTP " + res.status);
    if (location.hash !== "#caso/" + callId) return;   // el usuario ya abrió otro caso
    if (!datos.linea_tiempo) {
      throw new Error("el servidor del portal corre una versión anterior. Ciérralo (Ctrl+C) y vuelve a iniciarlo con python portal/server.py");
    }
    AppState.casoActual = datos;
    renderizarCaso(datos);
    vista.classList.remove("cargando");
  } catch (err) {
    document.getElementById("cp-meta").textContent = "No se pudo cargar el caso: " + err.message;
  }
}

function mostrarTabCaso(tab) {
  document.querySelectorAll(".caso-tab").forEach(b => b.classList.toggle("activa", b.dataset.tab === tab));
  document.querySelectorAll(".caso-panel").forEach(p => { p.hidden = p.dataset.tab !== tab; });
}

async function guardarNota() {
  const texto = document.getElementById("cp-nota-texto").value.trim();
  const error = document.getElementById("cp-nota-error");
  error.hidden = true;
  if (!texto || !AppState.casoActual) return;
  const callId = AppState.casoActual.caso.call_id;
  try {
    const res = await fetch("/api/caso/revisar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ call_id: callId, accion: "Comentario", comentario: texto }),
    });
    const r = await res.json();
    if (!res.ok) throw new Error(r.error || "Error HTTP " + res.status);
    document.getElementById("cp-nota-texto").value = "";
    await abrirCaso(callId);
    mostrarTabCaso("notas");
  } catch (err) {
    error.textContent = err.message;
    error.hidden = false;
  }
}

// ============================================================================
// DECISIÓN DEL ANALISTA SOBRE EL CASO
// ============================================================================
const TEXTO_ACCION = {
  "Confirmar": ["Confirmar tipología", "La tipología de la IA es correcta. El caso alimenta al Agente 2 con esa tipología.", "Comentario (opcional):"],
  "Corregir": ["Corregir tipología", "Elige la tipología correcta. El Agente 2 usará la tuya en su próxima corrida.", "Por qué la corriges (opcional):"],
  "Nueva modalidad": ["Marcar como nueva modalidad", "El caso no encaja en ninguna tipología del Manual. Queda como candidato a nueva modalidad para el Agente 2.", "Qué lo hace distinto (opcional):"],
  "Comentario": ["Agregar comentario", "Deja una nota en el historial del caso. No cambia la ficha.", "Comentario:"],
};

function abrirRevision(accion) {
  const datos = AppState.casoActual;
  if (!datos) return;
  AppState.accionRevision = accion;
  const [titulo, intro, etiqueta] = TEXTO_ACCION[accion];
  document.getElementById("rev-titulo").textContent = `${titulo} · ${codigoCaso(datos.caso.call_id)}`;
  document.getElementById("rev-intro").textContent = intro;
  document.getElementById("rev-etiqueta-comentario").textContent = etiqueta;
  document.getElementById("rev-grupo-tipologia").hidden = accion !== "Corregir";
  const actual = datos.caso.tipologia_analista || datos.caso.ai_tipificacion || datos.caso.modalidad_predicha;
  document.getElementById("rev-tipologia").innerHTML = Object.keys(NOMBRE_MODALIDAD)
    .filter(m => m !== "no_identificado" && m !== actual)
    .map(m => `<option value="${m}">${esc(nombreModalidad(m))}</option>`).join("");
  document.getElementById("rev-comentario").value = "";
  document.getElementById("rev-error").hidden = true;
  document.getElementById("modal-revision").classList.add("open");
}

function cerrarRevision() {
  document.getElementById("modal-revision").classList.remove("open");
}

async function guardarRevision() {
  const accion = AppState.accionRevision;
  const callId = AppState.casoActual.caso.call_id;
  const error = document.getElementById("rev-error");
  try {
    const res = await fetch("/api/caso/revisar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        call_id: callId, accion,
        tipologia: accion === "Corregir" ? document.getElementById("rev-tipologia").value : null,
        comentario: document.getElementById("rev-comentario").value.trim(),
      }),
    });
    const r = await res.json();
    if (!res.ok) throw new Error(r.error || "Error HTTP " + res.status);
    cerrarRevision();
    await Promise.all([abrirCaso(callId), cargarCasos()]);
  } catch (err) {
    error.textContent = err.message;
    error.hidden = false;
  }
}

// ============================================================================
// BARRA SUPERIOR: MIGAS DE PAN Y BÚSQUEDA
// ============================================================================
function actualizarMigas(vista, callId) {
  const partes = [`<a href="#resumen">Inicio</a>`];
  if (vista === "caso") {
    partes.push(`<a href="#reclamos">Casos analizados</a>`, `<span>${codigoCaso(callId)}</span>`);
  } else if (vista === "patron") {
    partes.push(`<a href="#patrones">Patrones e hipótesis</a>`, `<span>${esc(callId)}</span>`);
  } else if (vista !== "resumen") {
    partes.push(`<span>${esc(VISTAS[vista][0])}</span>`);
  }
  document.getElementById("migas").innerHTML = partes.join('<span class="migas-sep">›</span>');
}

function buscarGlobal(texto) {
  const t = texto.trim();
  if (!t) return;
  const numero = t.replace(/^c-?0*/i, "").replace(/^#/, "");
  if (/^\d+$/.test(numero) && AppState.casosPorId[parseInt(numero, 10)]) {
    irACaso(parseInt(numero, 10));
    return;
  }
  document.getElementById("casos-buscar").value = t;
  mostrarVista("reclamos");
  renderizarTablaCasos();
}

// ============================================================================
// PATTERN MEMORY
// ============================================================================
function textoClasificacion(cl) {
  if (!cl) return "los más críticos";
  const partes = [["Nuevo", "nuevos"], ["Conocido", "conocidos"], ["Bajo umbral", "bajo umbral"]]
    .filter(([k]) => cl[k]).map(([k, txt]) => `${cl[k]} ${txt}`);
  return partes.length ? partes.join(" · ") : "los más críticos";
}

function renderizarMemoriaDelPatron(p) {
  const cont = document.getElementById("pp-memoria");
  if (!p.clasificacion) {
    cont.innerHTML = '<p class="m-sec-text texto-tenue">Este patrón no tiene historial registrado.</p>';
    return;
  }
  const veces = p.veces_detectado || 1;
  const historia = veces > 1
    ? `Detectado en ${veces} corridas (desde la corrida ${p.primera_corrida}).`
    : `Primera detección (corrida ${p.primera_corrida ?? "—"}).`;
  const vistos = (p.memoria_casos_vistos || []).length;
  cont.innerHTML = `
    <div class="memoria-fila">${chipMemoria(p)}</div>
    <p class="m-sec-text">${esc(historia)} ${p.casos_nuevos ?? 0} casos nuevos en esta corrida;
      ${vistos} casos vistos en total.</p>
    ${p.decision_heredada ? '<p class="m-sec-text texto-tenue">El analista ya había decidido sobre este patrón y no hay evidencia nueva: la hipótesis hereda esa decisión. Puede cambiarse validándola de nuevo.</p>' : ""}
  `;
}

// ============================================================================
// TERMINALES DE RIESGO
// ============================================================================
const TIPOS_ENTIDAD = {
  destino_comun: {
    nombre: "Destino común",
    explica: "Aquí se cobraron varios fraudes. Indica un punto de cobro usado por los defraudadores.",
    accion: "Monitorear o bloquear cobros en este destino y revisar a quién pertenece.",
  },
  punto_de_compromiso: {
    nombre: "Punto de compromiso",
    explica: "Las víctimas compraron aquí días antes de sufrir el fraude. Indica dónde se robaron los datos de la tarjeta (por ejemplo, un skimmer).",
    accion: "Revisar el terminal y proteger preventivamente a los clientes que compraron aquí.",
  },
};

function abrirEntidades(entidades) {
  const cont = document.getElementById("lista-entidades");
  cont.innerHTML = entidades.length ? "" :
    '<p class="m-sec-text texto-tenue">No se detectaron terminales de riesgo en esta corrida.</p>';

  entidades.forEach(e => {
    const tipo = TIPOS_ENTIDAD[e.tipo] || { nombre: e.tipo || "—", explica: "", accion: "" };
    const esperado = e.esperado_por_azar !== undefined ? String(e.esperado_por_azar).replace(".", ",") : "—";
    const victimas = e.n_victimas ?? e.n_casos ?? "—";
    const modalidades = Object.entries(e.modalidades_a1 || {})
      .sort((a, b) => b[1] - a[1])
      .map(([m, n]) => `<span class="chip-patron">${esc(nombreModalidad(m))} · ${n}</span>`).join("");
    const idxPatron = AppState.patrones.findIndex(p => p.patron_id === e.patron_id);
    const etiquetaPatron = idxPatron >= 0 ? "P" + String(idxPatron + 1).padStart(2, "0") : null;

    const tarjeta = document.createElement("div");
    tarjeta.className = "entidad-card";
    tarjeta.innerHTML = `
      <div class="entidad-cabecera">
        <span class="entidad-nombre">${esc(e.entidad_id)}</span>
        <span class="chip-memoria ${e.tipo === "punto_de_compromiso" ? "bajo-umbral" : "conocido"}">${esc(tipo.nombre)}</span>
      </div>
      <p class="m-sec-text texto-tenue">${esc(e.comercio || "—")} · ${esc(e.ciudad || "—")} · canal ${esc(e.canal || "—")}</p>
      <p class="m-sec-text">${esc(tipo.explica)}</p>
      <div class="modal-grid-stats entidad-stats">
        <div class="m-stat"><span class="m-stat-label">Víctimas</span><span class="m-stat-val highlight-gold">${esc(victimas)}</span></div>
        <div class="m-stat"><span class="m-stat-label">Esperadas por azar</span><span class="m-stat-val">${esc(esperado)}</span></div>
        <div class="m-stat"><span class="m-stat-label">Casos vinculados</span><span class="m-stat-val">${esc(e.n_casos ?? "—")}</span></div>
        <div class="m-stat"><span class="m-stat-label">Tarjetas con compras legítimas ahí</span><span class="m-stat-val">${esc(e.tarjetas_que_usaron_el_terminal ?? "—")}</span></div>
      </div>
      <div class="m-sec-title entidad-sub">Modalidades que conecta</div>
      <div>${modalidades || '<span class="texto-tenue">—</span>'}</div>
      <p class="m-sec-text"><strong>Acción sugerida:</strong> ${esc(tipo.accion)}</p>
      ${etiquetaPatron ? `<button class="btn-secondary btn-ver-patron">Ver patrón ${etiquetaPatron} →</button>` : ""}
    `;
    const btn = tarjeta.querySelector(".btn-ver-patron");
    if (btn) btn.addEventListener("click", () => {
      cerrarEntidades();
      abrirModalDetalle(AppState.patrones[idxPatron], etiquetaPatron);
    });
    cont.appendChild(tarjeta);
  });
  document.getElementById("modal-entidades").classList.add("open");
}

function cerrarEntidades() {
  document.getElementById("modal-entidades").classList.remove("open");
}

// ============================================================================
// LENGUAJE DE NEGOCIO: HALLAZGO PRINCIPAL Y "POR QUÉ LO CREEMOS"
// (todo se calcula con los datos guardados; no se llama a la IA)
// ============================================================================
const NOMBRE_MODALIDAD = {
  phishing_vishing: "phishing/vishing", sim_swap: "SIM swap", toma_de_cuenta: "toma de cuenta",
  tarjeta_clonada: "tarjeta clonada", comercio_comprometido: "comercio comprometido",
  robo_perdida_tarjeta: "robo o pérdida de tarjeta", suscripcion_no_autorizada: "suscripción no autorizada",
  fraude_amigable: "fraude amigable", no_identificado: "no identificado",
  no_fraude: "un caso que no es fraude", nueva_modalidad: "una modalidad nueva",
};
const nombreModalidad = m => NOMBRE_MODALIDAD[m] || m;

const FRASE_RASGO = {
  canal_transaccion: { APP: "se hizo por la app", INT: "se hizo por internet", POS: "fue presencial (POS)" },
  modo_entrada: { BANDA: "se leyó por banda magnética", FALLBACK: "se leyó en modo fallback (sin chip)",
                  CONTACTLESS: "fue sin contacto", CHIP: "se leyó con chip", NO_PRESENTE: "fue sin la tarjeta física",
                  APP: null },
  franja_horaria: { madrugada: "ocurrió de madrugada", manana: "ocurrió en la mañana", tarde: "ocurrió en la tarde", noche: "ocurrió de noche" },
  tipo_tarjeta: { debito: "fue con tarjeta de débito", credito: "fue con tarjeta de crédito" },
};
const FRASE_ANOMALIA = {
  canal_nuevo: "el cliente usó un canal que nunca había usado",
  ciudad_nueva: "la compra fue en una ciudad donde la tarjeta nunca había comprado",
  modo_riesgoso: "la tarjeta se leyó por banda magnética o fallback, que son fáciles de clonar",
  monto_atipico: "el monto fue muy superior a lo habitual de ese cliente",
};
const pct = v => `${Math.round(v)} %`;

function fraseRasgo(r) {
  if (r.rasgo === "monto") return `Montos ${r.valor}s: la mitad de los casos está por ${r.valor === "bajo" ? "debajo" : "encima"} de ${formatoSoles(r.mediana_grupo)}.`;
  let accion;
  if (FRASE_RASGO[r.rasgo]) accion = FRASE_RASGO[r.rasgo][r.valor];
  else if (r.rasgo === "ciudad") accion = `afectó a clientes de ${r.valor}`;
  else if (r.rasgo === "ciudad_comercio") accion = r.valor === "Online" ? "fue en comercios online" : `fue en comercios de ${r.valor}`;
  else if (r.rasgo === "comercio") accion = `fue en comercios de ${r.valor.toLowerCase()}`;
  if (!accion) return null;
  return `El ${pct(r.pct_grupo)} ${accion} (en el total de reclamos, ${pct(r.pct_universo)}).`;
}

function razonesDelPatron(p) {
  const ev = p.evidencia_json || {};
  const razones = [];
  const ent = ev.entidad_de_riesgo;
  if (ent) {
    const victimas = ent.n_victimas ?? ent.n_casos;
    const esperado = String(ent.esperado_por_azar).replace(".", ",");
    razones.push(ent.tipo === "punto_de_compromiso"
      ? `${victimas} víctimas habían comprado en ${ent.entidad_id} días antes del fraude; por casualidad se esperaban ${esperado}.`
      : `${victimas} víctimas tuvieron cobros fraudulentos en ${ent.entidad_id}; por casualidad se esperaban ${esperado}.`);
  }
  const modalidades = Object.entries(ev.modalidades || {}).sort((x, y) => y[1] - x[1]);
  if (modalidades.length > 1) {
    razones.push(`Agrupa ${modalidades.length} tipos de fraude: ` +
      modalidades.map(([m, n]) => `${nombreModalidad(m)} (${n})`).join(", ") + ".");
  }
  const usadas = new Set();
  for (const r of ev.rasgos_distintivos || []) {
    if (razones.length >= 4) break;
    const frase = fraseRasgo(r);
    // canal y modo de entrada suelen decir lo mismo (APP/APP, INT/NO_PRESENTE): se muestra una sola vez
    const clave = `${r.pct_grupo}-${r.casos}`;
    if (!frase || usadas.has(clave)) continue;
    usadas.add(clave);
    razones.push(frase);
    if (razones.filter(x => x.startsWith("El ")).length >= 2) break;
  }
  const anomalias = Object.entries(ev.anomalias || {}).sort((a, b) =>
    (b[1].pct_casos - b[1].pct_legitimas) - (a[1].pct_casos - a[1].pct_legitimas));
  if (anomalias.length && FRASE_ANOMALIA[anomalias[0][0]]) {
    const [nombre, v] = anomalias[0];
    razones.push(`En el ${pct(v.pct_casos)} de los casos ${FRASE_ANOMALIA[nombre]}; en compras normales pasa solo en el ${String(v.pct_legitimas).replace(".", ",")} %.`);
  }
  razones.push(`Involucra ${p.num_casos} reclamos por ${formatoSoles(p.impacto_total)}.`);
  return razones;
}

function renderizarHallazgo(patrones, embudo) {
  const caja = document.getElementById("hallazgo-principal");
  if (!patrones.length) { caja.hidden = true; return; }

  // Prioridad: un comercio sospechoso con más víctimas (evidencia más fuerte); si no hay, el de mayor monto
  const conEntidad = patrones.filter(p => p.evidencia_json && p.evidencia_json.entidad_de_riesgo)
    .sort((a, b) => (b.evidencia_json.entidad_de_riesgo.n_victimas || 0) - (a.evidencia_json.entidad_de_riesgo.n_victimas || 0));
  const elegido = conEntidad[0] || [...patrones].sort((a, b) => b.impacto_total - a.impacto_total)[0];
  const idx = AppState.patrones.findIndex(p => p.patron_id === elegido.patron_id);
  const ent = elegido.evidencia_json && elegido.evidencia_json.entidad_de_riesgo;
  const detalle = (embudo.entidades_detalle || []).find(e => ent && e.entidad_id === ent.entidad_id) || {};

  let titulo, texto, reco;
  if (ent) {
    const tipos = Object.keys(ent.modalidades_a1 || {}).length;
    const victimas = ent.n_victimas ?? ent.n_casos;
    const esperado = String(ent.esperado_por_azar).replace(".", ",");
    if (ent.tipo === "punto_de_compromiso") {
      titulo = `${victimas} víctimas de fraude habían comprado antes en el mismo comercio: ${ent.entidad_id}`;
      texto = `Es un ${String(detalle.comercio || "comercio").toLowerCase()} en ${detalle.ciudad || "—"}. Por casualidad se esperaba menos de una víctima. ` +
        `Después, sus tarjetas se usaron en fraudes por ${formatoSoles(elegido.impacto_total)}.`;
    } else {
      titulo = `${elegido.num_casos} fraudes de ${tipos} tipo${tipos === 1 ? "" : "s"} distinto${tipos === 1 ? "" : "s"} se cobraron en el mismo destino: ${ent.entidad_id}`;
      texto = `En la atención al cliente se vieron como reclamos separados (${Object.keys(ent.modalidades_a1 || {}).map(nombreModalidad).join(", ")}). ` +
        `Por casualidad se esperaban ${esperado}. Monto involucrado: ${formatoSoles(elegido.impacto_total)}.`;
    }
    reco = "Recomendación: " + ((TIPOS_ENTIDAD[ent.tipo] || {}).accion || elegido.accion_recomendada || "");
  } else {
    titulo = elegido.titulo;
    texto = `${elegido.num_casos} reclamos por ${formatoSoles(elegido.impacto_total)}.`;
    reco = elegido.accion_recomendada ? "Recomendación: " + elegido.accion_recomendada : "";
  }
  document.getElementById("hallazgo-titulo").textContent = titulo;
  document.getElementById("hallazgo-texto").textContent = texto;
  document.getElementById("hallazgo-reco").textContent = reco;
  document.getElementById("hallazgo-btn").onclick = () =>
    abrirModalDetalle(AppState.patrones[idx], "P" + String(idx + 1).padStart(2, "0"));
  caja.hidden = false;

  // Dinero en juego: casos únicos (un reclamo puede estar en más de un patrón)
  const unicos = new Set(patrones.flatMap(p => p.casos_asociados || []));
  const monto = [...unicos].reduce((suma, id) => suma + parseFloat((AppState.casosPorId[id] || {}).amount || 0), 0);
  document.getElementById("resumen-dinero").textContent =
    `${unicos.size} reclamos por ${formatoSoles(monto)} quedaron explicados por ${patrones.length} patrones de fraude.`;
}

// ============================================================================
// PRIORIDAD, RIESGO Y ESTADO DE LA FICHA
// ============================================================================
function chipPrioridad(prioridad, detalle) {
  if (!prioridad) return '<span class="texto-tenue">—</span>';
  const puntos = (detalle && detalle.puntos) || {};
  const nombres = { impacto: "impacto", volumen: "15+ reclamos", confianza: "confianza ≥ 85 %", comercio_sospechoso: "comercio sospechoso" };
  const motivo = Object.entries(puntos).filter(([, v]) => v > 0).map(([k, v]) => `${nombres[k] || k} (+${v})`).join(", ");
  return `<span class="chip-nivel ${prioridad.toLowerCase()}" title="${esc("Puntos: " + (motivo || "ninguno"))}">${esc(prioridad)}</span>`;
}

function chipRiesgo(c) {
  if (!c.nivel_riesgo) return "";
  return `<span class="chip-nivel ${c.nivel_riesgo.toLowerCase()}" title="${esc((c.riesgo_razones || []).join(" · "))}">${esc(c.nivel_riesgo)}</span>`;
}

function chipEstadoFicha(c) {
  if (!c.estado_ficha) return "";
  const clase = { "Completo": "completo", "Incompleto": "incompleto", "Sin tipología": "sin-tipologia" }[c.estado_ficha] || "";
  return `<span class="chip-ficha ${clase}" title="${esc(c.estado_motivo || "")}">${esc(c.estado_ficha)}</span>`;
}

function textoDetalleConfianza(d) {
  if (!d || d.confianza_a1_promedio === undefined) return "";
  const consistencia = d.consistencia !== undefined
    ? `Consistencia ${d.consistencia} (mayor entre pureza ${d.pureza_modalidad} y fuerza estadística ${d.fuerza_estadistica})`
    : `Pureza de modalidad ${d.pureza_modalidad}`;
  return `Seguridad de la IA ${d.confianza_a1_promedio} × 50 % + ${consistencia} × 30 % + Cantidad de casos ${d.soporte} × 20 %`;
}

// ============================================================================
// VARIABLES MÁS RELEVANTES (lo que distingue al patrón del resto)
// Relevancia = 1 − (proporción en el total / proporción en el patrón): 0 si no lo distingue,
// 1 si solo aparece en el patrón.
// ============================================================================
const ETIQUETA_RASGO = {
  canal_transaccion: v => ({ APP: "Canal: app", INT: "Canal: internet", POS: "Canal: presencial (POS)" }[v] || `Canal: ${v}`),
  modo_entrada: v => ({ BANDA: "Lectura por banda magnética", FALLBACK: "Lectura en modo fallback", CONTACTLESS: "Pago sin contacto",
                        CHIP: "Lectura con chip", NO_PRESENTE: "Sin tarjeta física", APP: "Pago desde la app" }[v] || `Modo: ${v}`),
  ciudad: v => `Clientes de ${v}`,
  ciudad_comercio: v => v === "Online" ? "Comercios online" : `Comercios en ${v}`,
  comercio: v => `Rubro: ${v}`,
  franja_horaria: v => `Horario: ${({ manana: "mañana" })[v] || v}`,
  tipo_tarjeta: v => `Tarjeta de ${({ debito: "débito", credito: "crédito" })[v] || v}`,
};
const ETIQUETA_ANOMALIA = {
  canal_nuevo: "Canal que el cliente nunca había usado",
  ciudad_nueva: "Ciudad donde la tarjeta nunca había comprado",
  modo_riesgoso: "Lectura clonable (banda o fallback)",
  monto_atipico: "Monto muy superior al habitual",
};

function variablesDelPatron(p) {
  const ev = p.evidencia_json || {};
  const vars = [];
  const ent = ev.entidad_de_riesgo;
  if (ent) {
    const victimas = ent.n_victimas ?? ent.n_casos;
    vars.push({ etiqueta: `Comercio sospechoso: ${ent.entidad_id}`, valor: Math.max(0, 1 - ent.esperado_por_azar / victimas),
                detalle: `${victimas} víctimas frente a ${String(ent.esperado_por_azar).replace(".", ",")} esperadas por casualidad` });
  }
  const vistos = new Set();
  for (const r of ev.rasgos_distintivos || []) {
    if (!ETIQUETA_RASGO[r.rasgo] || !r.pct_grupo) continue;
    const clave = `${r.pct_grupo}-${r.casos}`;   // canal y modo de entrada suelen repetir la misma señal
    if (vistos.has(clave)) continue;
    vistos.add(clave);
    vars.push({ etiqueta: ETIQUETA_RASGO[r.rasgo](r.valor), valor: Math.max(0, 1 - r.pct_universo / r.pct_grupo),
                detalle: `${pct(r.pct_grupo)} del patrón frente a ${pct(r.pct_universo)} del total` });
  }
  for (const [nombre, a] of Object.entries(ev.anomalias || {})) {
    if (!ETIQUETA_ANOMALIA[nombre] || !a.pct_casos) continue;
    vars.push({ etiqueta: ETIQUETA_ANOMALIA[nombre], valor: Math.max(0, 1 - a.pct_legitimas / a.pct_casos),
                detalle: `${pct(a.pct_casos)} del patrón frente a ${String(a.pct_legitimas).replace(".", ",")} % en compras normales` });
  }
  return vars.sort((a, b) => b.valor - a.valor).slice(0, 5);
}

function renderizarVariables(p) {
  const vars = variablesDelPatron(p);
  const colores = ["verde", "verde", "azul", "celeste", "celeste"];
  document.getElementById("pp-variables").innerHTML = vars.length ? vars.map((v, k) => `
    <div class="peso-fila" title="${esc(v.detalle)}">
      <span class="peso-nombre">${esc(v.etiqueta)}<span class="peso-detalle">${esc(v.detalle)}</span></span>
      <span class="peso-pista"><span class="peso-barra ${colores[k]}" style="width:${Math.round(v.valor * 100)}%"></span></span>
      <span class="peso-valor">${Math.round(v.valor * 100)} %</span>
    </div>`).join("") : '<p class="m-sec-text texto-tenue">Sin variables distintivas para este patrón.</p>';
}

// ============================================================================
// GRÁFICOS: barras horizontales de una sola serie (sin legenda; el título dice qué se mide)
// ============================================================================
function graficoBarras(contenedorId, filas, alClic) {
  const cont = document.getElementById(contenedorId);
  const maximo = Math.max(...filas.map(f => f.valor), 1);
  cont.innerHTML = filas.map((f, i) => `
    <div class="barra-fila" data-i="${i}">
      <span class="barra-etiqueta">${esc(f.etiqueta)}</span>
      <span class="barra-pista"><span class="barra-relleno" style="width:${Math.max(1, f.valor / maximo * 100).toFixed(1)}%"></span></span>
      <span class="barra-valor">${esc(f.texto)}</span>
    </div>`).join("");
  const tooltip = document.getElementById("tooltip-grafico");
  cont.querySelectorAll(".barra-fila").forEach(el => {
    const f = filas[Number(el.dataset.i)];
    el.addEventListener("mousemove", e => {
      tooltip.innerHTML = f.tooltip;
      tooltip.hidden = false;
      tooltip.style.left = `${e.clientX + 14}px`;
      tooltip.style.top = `${e.clientY + 14}px`;
    });
    el.addEventListener("mouseleave", () => { tooltip.hidden = true; });
    if (alClic) el.addEventListener("click", () => { tooltip.hidden = true; alClic(f); });
  });
}

function renderizarGraficos(patrones, casos) {
  const porPatron = patrones.map((p, i) => ({
    idx: i,
    etiqueta: `P${String(i + 1).padStart(2, "0")} · ${(p.evidencia_json && p.evidencia_json.entidad_de_riesgo)
      ? p.evidencia_json.entidad_de_riesgo.entidad_id : nombreModalidad(p.modalidad_asociada)}`,
    valor: parseFloat(p.impacto_total || 0),
    texto: `${formatoSoles(p.impacto_total)}${p.prioridad ? ` (${p.prioridad})` : ""}`,
    tooltip: `<strong>${esc(p.titulo)}</strong><br>${p.num_casos} reclamos · ${formatoSoles(p.impacto_total)}<br>` +
             `Confianza ${formatoPct(p.confianza)} · Prioridad ${esc(p.prioridad || "—")}<br><span class="texto-tenue">Clic para ver el detalle</span>`,
  })).sort((a, b) => b.valor - a.valor);
  graficoBarras("grafico-patrones", porPatron, f =>
    abrirModalDetalle(AppState.patrones[f.idx], "P" + String(f.idx + 1).padStart(2, "0")));

  const tipos = {};
  casos.forEach(c => {
    const t = tipos[c.modalidad] = tipos[c.modalidad] || { n: 0, monto: 0, ok: 0 };
    t.n += 1; t.monto += parseFloat(c.amount || 0); t.ok += c.correcta === true ? 1 : 0;
  });
  const porTipo = Object.entries(tipos).map(([m, t]) => ({
    modalidad: m,
    etiqueta: nombreModalidad(m),
    valor: t.n,
    texto: `${t.n} · ${formatoSoles(t.monto)}`,
    tooltip: `<strong>${esc(nombreModalidad(m))}</strong><br>${t.n} reclamos · ${formatoSoles(t.monto)}<br>` +
             `Acierto de la IA: ${t.ok} de ${t.n}<br><span class="texto-tenue">Clic para filtrar la tabla de reclamos</span>`,
  })).sort((a, b) => b.valor - a.valor);
  graficoBarras("grafico-tipos", porTipo, f => {
    const sel = document.getElementById("casos-filtro-modalidad");
    sel.value = f.modalidad;
    renderizarTablaCasos();
    mostrarVista("reclamos");
  });
}

// ============================================================================
// CALIDAD DEL AGENTE 1
// ============================================================================
const NOMBRE_CORTO = {
  comercio_comprometido: "Comercio comp.", phishing_vishing: "Phishing", robo_perdida_tarjeta: "Robo/pérdida",
  sim_swap: "SIM swap", tarjeta_clonada: "T. clonada", toma_de_cuenta: "Toma de cuenta",
  suscripcion_no_autorizada: "Suscripción", fraude_amigable: "F. amigable", no_identificado: "No ident.",
};
const pctCalidad = v => v === null || v === undefined ? "—" : `${(v * 100).toFixed(1).replace(".", ",")} %`;

function renderizarCalidad(q) {
  if (!q || !q.total) {
    document.getElementById("calidad-badge").textContent = "sin datos";
    document.getElementById("calidad-hallazgo").textContent = q
      ? "Todavía no hay reclamos analizados con modalidad real para evaluar."
      : "El servidor no envió las métricas. Si se actualizó el portal, reinícialo (Ctrl+C y python portal/server.py).";
    return;
  }
  document.getElementById("calidad-badge").textContent = `${q.total} reclamos evaluados`;

  const tiles = [
    ["Exactitud", pctCalidad(q.exactitud), `${q.correctas} de ${q.total} reclamos bien clasificados`],
    ["Precisión promedio", pctCalidad(q.precision_macro), "promedio entre modalidades"],
    ["Recall promedio", pctCalidad(q.recall_macro), "promedio entre modalidades"],
    ["Confianza de la IA", q.confianza_aciertos !== null
      ? `${Number(q.confianza_aciertos).toFixed(1).replace(".", ",")} vs ${Number(q.confianza_errores).toFixed(1).replace(".", ",")}` : "—",
     `en aciertos vs en errores (${q.casos_con_confianza} casos con puntaje)`],
  ];
  document.getElementById("calidad-kpis").innerHTML = tiles.map(([t, v, s]) => `
    <div class="calidad-kpi"><span class="kpi-label">${esc(t)}</span><span class="calidad-kpi-valor">${esc(v)}</span>
      <span class="texto-tenue">${esc(s)}</span></div>`).join("");

  const e = (q.errores_frecuentes || [])[0];
  document.getElementById("calidad-hallazgo").textContent = e
    ? `Principal confusión: ${e.casos} reclamo${e.casos === 1 ? "" : "s"} de ${nombreModalidad(e.real)} ` +
      `${e.casos === 1 ? "fue clasificado" : "fueron clasificados"} como ${nombreModalidad(e.predicha)} ` +
      `(llamadas ${e.call_ids.map(id => "#" + id).join(", ")}). Cuando se equivoca, la IA declara menos confianza.`
    : "La IA no tuvo errores de clasificación.";

  document.getElementById("tbody-metricas").innerHTML = q.por_modalidad
    .filter(x => x.casos_reales || x.predichos)
    .map(x => `
      <tr>
        <td>${esc(nombreModalidad(x.modalidad))}</td>
        <td>${x.casos_reales}</td>
        <td>${x.verdaderos_positivos}</td>
        <td class="${x.falsos_positivos ? "valor-alerta" : ""}">${x.falsos_positivos}</td>
        <td class="${x.falsos_negativos ? "valor-alerta" : ""}">${x.falsos_negativos}</td>
        <td>${pctCalidad(x.precision)}</td>
        <td>${pctCalidad(x.recall)}</td>
        <td>${x.f1 === null ? "—" : x.f1.toFixed(2).replace(".", ",")}</td>
      </tr>`).join("");

  // Matriz de confusión: un solo tono, más intenso cuantos más casos (escala secuencial)
  const max = Math.max(...q.matriz.flat(), 1);
  const corto = m => NOMBRE_CORTO[m] || nombreModalidad(m);
  const cabecera = `<tr><th class="mc-esquina">Real ↓ · IA →</th>${q.etiquetas.map(m => `<th title="${esc(nombreModalidad(m))}">${esc(corto(m))}</th>`).join("")}</tr>`;
  const filas = q.etiquetas.map((real, i) => `
    <tr><th class="mc-fila" title="${esc(nombreModalidad(real))}">${esc(corto(real))}</th>${q.matriz[i].map((v, j) => {
      const alpha = v ? 0.14 + 0.76 * (v / max) : 0;
      const ids = q.matriz_casos[i][j];
      const titulo = `Real: ${nombreModalidad(real)} · IA: ${nombreModalidad(q.etiquetas[j])} · ${v} reclamo${v === 1 ? "" : "s"}` +
        (ids.length && i !== j ? ` (${ids.map(x => "#" + x).join(", ")})` : "");
      return `<td class="mc-celda${i === j ? " diagonal" : ""}${v && i !== j ? " error" : ""}" title="${esc(titulo)}"
        style="background-color: rgba(47, 143, 216, ${alpha.toFixed(2)}); color: ${alpha > 0.5 ? "#ffffff" : "var(--text-muted)"}">${v || "·"}</td>`;
    }).join("")}</tr>`).join("");
  document.getElementById("matriz-confusion").innerHTML = `<table class="matriz-tabla">${cabecera}${filas}</table>`;
}

// ============================================================================
// PESTAÑAS (VISTAS)
// ============================================================================
const VISTAS = {
  resumen: ["Resumen", "Visión general de la actividad de prevención de fraude"],
  patrones: ["Patrones e hipótesis", "¿Qué patrones hay y cuáles valida el analista?"],
  red: ["Grafo de relaciones", "Exploración visual de conexiones entre comercios, canales, ciudades y tipologías de fraude"],
  reclamos: ["Casos analizados", "¿Qué hizo la IA con cada llamada y qué decidió el analista?"],
  calidad: ["Calidad del Agente 1", "¿Qué tan confiable es la IA al clasificar?"],
  config: ["Configuración", "Parámetros de negocio, catálogo de modalidades y trazabilidad de las corridas"],
  caso: ["Detalle de caso", ""],
};

function mostrarVista(nombre, actualizarHash = true) {
  // Detalle de caso: #caso/58
  const enCaso = /^caso\/(\d+)$/.exec(nombre || "");
  if (enCaso) {
    document.querySelectorAll(".vista").forEach(v => { v.hidden = v.dataset.vista !== "caso"; });
    document.querySelectorAll(".nav-item").forEach(b => b.classList.toggle("active", b.dataset.vista === "reclamos"));
    document.getElementById("banner-vista").hidden = true;
    document.getElementById("tooltip-grafico").hidden = true;
    mostrarTabCaso("detalle");
    actualizarMigas("caso", enCaso[1]);
    window.scrollTo({ top: 0 });
    abrirCaso(parseInt(enCaso[1], 10));
    return;
  }
  const enPatron = /^patron\/(\d+)$/.exec(nombre || "");
  if (enPatron) {
    document.querySelectorAll(".vista").forEach(v => { v.hidden = v.dataset.vista !== "patron"; });
    document.querySelectorAll(".nav-item").forEach(b => b.classList.toggle("active", b.dataset.vista === "patrones"));
    document.getElementById("banner-vista").hidden = true;
    document.getElementById("tooltip-grafico").hidden = true;
    window.scrollTo({ top: 0 });
    abrirPatron(parseInt(enPatron[1], 10));
    return;
  }
  if (!VISTAS[nombre] || nombre === "caso" || nombre === "patron") nombre = "resumen";
  document.getElementById("banner-vista").hidden = false;
  actualizarMigas(nombre);
  document.querySelectorAll(".vista").forEach(v => { v.hidden = v.dataset.vista !== nombre; });
  document.querySelectorAll(".nav-item").forEach(b => b.classList.toggle("active", b.dataset.vista === nombre));
  document.getElementById("titulo-vista").textContent = VISTAS[nombre][0];
  document.getElementById("pregunta-vista").textContent = VISTAS[nombre][1];
  document.getElementById("tooltip-grafico").hidden = true;
  if (actualizarHash && location.hash.slice(1) !== nombre) history.replaceState(null, "", "#" + nombre);
  window.scrollTo({ top: 0 });
  if (nombre === "config") cargarConfiguracion();
  // El mapa se dibuja con el tamaño real recién al hacerse visible
  if (nombre === "red") renderizarGrafo();
}

// ============================================================================
// CONFIGURACIÓN: PARÁMETROS DE NEGOCIO Y CATÁLOGO VIGENTE
// ============================================================================
const NOMBRE_PARAMETRO = {
  ventana_dias: "Ventana de fichas (días)",
  min_casos_patron: "Mínimo de casos por patrón",
  min_casos_nuevos_evidencia: "Casos nuevos para reproponer un descartado",
  min_casos_entidad: "Mínimo de víctimas por comercio sospechoso",
  umbral_impacto_alto: "Impacto alto desde (S/)",
  umbral_impacto_medio: "Impacto medio desde (S/)",
  modalidades_excluidas: "Modalidades excluidas",
};

async function cargarConfiguracion() {
  try {
    const res = await fetch("/api/configuracion");
    const d = await res.json();
    if (!res.ok) throw new Error(d.error || "Error HTTP " + res.status);
    AppState.config = d;
    renderizarConfiguracion(d);
  } catch (err) {
    document.getElementById("cfg-parametros").innerHTML =
      `<p class="rev-error">No se pudo cargar la configuración: ${esc(err.message)}. Si acabas de actualizar, reinicia el portal.</p>`;
  }
}

function renderizarConfiguracion({ parametros, catalogo, corrida_a1, corrida_a2 }) {
  const modalidades = catalogo.modalidades.map(m => m.clave);
  document.getElementById("cfg-parametros").innerHTML = parametros.map(p => {
    const campo = p.clave === "modalidades_excluidas"
      ? `<div class="cfg-checks" data-clave="${p.clave}">${modalidades.map(m => `
          <label><input type="checkbox" value="${m}" ${p.valor.split(",").includes(m) ? "checked" : ""}> ${esc(nombreModalidad(m))}</label>`).join("")}</div>`
      : p.clave === "ventana_dias"
        ? `<select class="form-input-sm" data-clave="${p.clave}">${[["0", "Todas las fichas"], ["30", "30 días"], ["60", "60 días"], ["90", "90 días"]]
            .map(([v, t]) => `<option value="${v}" ${p.valor === v ? "selected" : ""}>${t}</option>`).join("")}
            ${["0", "30", "60", "90"].includes(p.valor) ? "" : `<option value="${esc(p.valor)}" selected>${esc(p.valor)} días</option>`}</select>`
        : `<input class="form-input-sm" type="number" min="0" data-clave="${p.clave}" value="${esc(p.valor)}">`;
    return `<div class="cfg-fila">
      <div><span class="cfg-nombre">${esc(NOMBRE_PARAMETRO[p.clave] || p.clave)}</span>
        <span class="cfg-desc">${esc(p.descripcion || "")}</span>
        ${p.actualizado_por ? `<span class="cfg-desc">Cambiado por ${esc(p.actualizado_por)} el ${esc(fechaCorta(p.updated_at))}</span>` : ""}</div>
      <div class="cfg-campo">${campo}</div></div>`;
  }).join("");

  const filaCorrida = (titulo, c, extra) => c
    ? `<div class="cfg-corrida"><strong>${titulo}</strong><span>${extra}</span></div>`
    : `<div class="cfg-corrida"><strong>${titulo}</strong><span class="sin-dato">Aún no hay corridas registradas con esta versión.</span></div>`;
  const p2 = (corrida_a2 && corrida_a2.parametros) || {};
  document.getElementById("cfg-corridas").innerHTML =
    filaCorrida("Agente 1", corrida_a1, corrida_a1 && `Corrida ${esc(corrida_a1.corrida_a1_id)} · ${esc(fechaCorta(corrida_a1.inicio))} ·
      ${esc(corrida_a1.n_casos ?? "—")} casos (${esc(corrida_a1.n_fallidos ?? 0)} fallidos) · Manual v${esc(corrida_a1.version_manual)} ·
      instrucciones ${esc(corrida_a1.prompt_version)}`) +
    filaCorrida("Agente 2", corrida_a2, corrida_a2 && `Corrida ${esc(corrida_a2.corrida_id)} · ${esc(fechaCorta(corrida_a2.created_at))} ·
      ${esc(corrida_a2.n_casos)} casos · ${Object.keys(p2).length
        ? `ventana ${p2.ventana_dias ? esc(p2.ventana_dias) + " días" : "completa"}, mínimo ${esc(p2.min_casos_patron)} casos por patrón`
        : '<span class="sin-dato">corrida anterior al registro de parámetros</span>'}`) +
    `<div class="cfg-corrida"><strong>Fichas por versión del Manual</strong><span>${catalogo.fichas_por_version
      .map(f => `${esc(f.version === "no registrada" ? "no registrada" : "v" + f.version)}: ${f.fichas}`).join(" · ")}</span></div>`;

  document.getElementById("cfg-catalogo-titulo").textContent = `Catálogo de modalidades vigente · Manual v${catalogo.version}`;
  document.getElementById("cfg-catalogo-nota").textContent = `${catalogo.modalidades.length} modalidades`;
  const claseRiesgo = { BAJO: "bajo", MEDIO: "medio", ALTO: "alto", CRITICO: "alto" };
  document.getElementById("cfg-catalogo").innerHTML = catalogo.modalidades.map(m => `
    <tr><td><strong>${esc(m.nombre)}</strong><br><span class="texto-tenue texto-mono">${esc(m.clave)}</span>
        ${m.riesgo_base ? `<br><span class="chip-nivel ${claseRiesgo[m.riesgo_base] || ""}">Riesgo base ${esc(m.riesgo_base.toLowerCase())}</span>` : ""}
        ${(m.equivalencias_poc || []).length ? `<br><span class="texto-tenue cfg-equiv">En el manual PoC: ${esc(m.equivalencias_poc.join(", "))}</span>` : ""}</td>
      <td>${esc(m.definicion)}
        ${(m.frases_cliente || []).length ? `<span class="cfg-frases"><b>El cliente suele decir:</b> ${m.frases_cliente.map(f => `«${esc(f)}»`).join(" · ")}</span>` : ""}
        ${Object.entries(m.diferenciar_de || {}).map(([o, t]) => `<span class="cfg-dif"><b>No confundir con ${esc(nombreModalidad(o))}:</b> ${esc(t)}</span>`).join("")}</td>
      <td>${esc(m.accion_recomendada)}</td></tr>`).join("");
  document.getElementById("cfg-no-fraude").innerHTML = (catalogo.no_fraude || []).map(n => `
    <tr><td><strong>${esc(n.id)}</strong> · ${esc(n.nombre)}</td><td>${esc((n.senales || []).join(" · "))}</td><td>${esc(n.accion)}</td></tr>`).join("")
    || '<tr><td colspan="3" class="texto-tenue">El manual no define casos de no fraude.</td></tr>';
  document.getElementById("cfg-reglas").innerHTML = (catalogo.reglas || []).map(r => `<li>${esc(r)}</li>`).join("")
    || '<li class="texto-tenue">El manual no define reglas generales.</li>';
  if (catalogo.notas_version) document.getElementById("cfg-catalogo-nota").title = catalogo.notas_version;
}

async function guardarConfiguracion() {
  const error = document.getElementById("cfg-error"), ok = document.getElementById("cfg-ok");
  error.hidden = ok.hidden = true;
  const actuales = Object.fromEntries((AppState.config?.parametros || []).map(p => [p.clave, p.valor]));
  const cambios = {};
  document.querySelectorAll("#cfg-parametros [data-clave]").forEach(el => {
    const valor = el.classList.contains("cfg-checks")
      ? [...el.querySelectorAll("input:checked")].map(i => i.value).join(",")
      : el.value.trim();
    const normal = v => (/^-?\d+(\.\d+)?$/.test(v) ? String(parseFloat(v)) : v);
    if (normal(valor) !== normal(actuales[el.dataset.clave] ?? "")) cambios[el.dataset.clave] = valor;
  });
  if (!Object.keys(cambios).length) { error.textContent = "No hay cambios que guardar."; error.hidden = false; return; }
  try {
    const res = await fetch("/api/parametros", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cambios }),
    });
    const r = await res.json();
    if (!res.ok) throw new Error(r.error || "Error HTTP " + res.status);
    await cargarConfiguracion();
    ok.hidden = false;
  } catch (err) {
    error.textContent = err.message;
    error.hidden = false;
  }
}

// ============================================================================
// GRAFO DE RELACIONES (lámina 78, pantalla 6): comercios, canales, ciudades y tipologías
// Se arma con los casos analizados, así los filtros por periodo, tipología y patrón son reales.
// Un nodo en el centro y, alrededor, lo que comparte casos con él; clic en un nodo para centrarlo.
// ============================================================================
// Sectores en grados (0° = derecha, 90° = arriba), como en la maqueta
const GRUPOS_GRAFO = {
  canal:     { nombre: "Canal", plural: "Canales", color: "#22a35a", sector: [58, 122], icono: "pantalla" },
  tipologia: { nombre: "Tipología", plural: "Tipologías", color: "#ea7a1a", sector: [-55, 35], icono: "alerta" },
  ciudad:    { nombre: "Ciudad del comercio", plural: "Ciudades", color: "#0e8fae", sector: [232, 308], icono: "ubicacion" },
  comercio:  { nombre: "Comercio", plural: "Comercios", color: "#2563eb", sector: [145, 215], icono: "tienda" },
};
const NOMBRE_CANAL = { POS: "POS presencial", INT: "Internet", APP: "App del banco" };
const MAX_POR_GRUPO = 6;

function poblarFiltrosGrafo() {
  const selT = document.getElementById("g-tipologia"), selP = document.getElementById("g-patron");
  const t = selT.value, p = selP.value;
  const tipos = [...new Set(AppState.casos.map(c => c.tipologia_analista || c.modalidad).filter(Boolean))].sort();
  selT.innerHTML = '<option value="">Todas las tipologías</option>' +
    tipos.map(m => `<option value="${esc(m)}">${esc(nombreModalidad(m))}</option>`).join("");
  selP.innerHTML = '<option value="">Todos los casos</option>' + AppState.patrones.map((x, i) =>
    `<option value="${x.patron_id}">P${String(i + 1).padStart(2, "0")} · ${esc(x.titulo.slice(0, 48))}${x.titulo.length > 48 ? "…" : ""}</option>`).join("");
  selT.value = tipos.includes(t) ? t : "";
  selP.value = AppState.patrones.some(x => String(x.patron_id) === p) ? p : "";
}

function casosDelGrafo() {
  const dias = parseInt(document.getElementById("g-periodo").value, 10);
  const tipo = document.getElementById("g-tipologia").value;
  const patron = AppState.patrones.find(x => String(x.patron_id) === document.getElementById("g-patron").value);
  const desde = dias ? new Date(Date.now() - dias * 864e5).toISOString().slice(0, 10) : null;
  const ids = patron ? new Set(patron.casos_asociados || []) : null;
  return AppState.casos.filter(c =>
    (!desde || String(c.fecha_llamada || "").slice(0, 10) >= desde) &&
    (!tipo || (c.tipologia_analista || c.modalidad) === tipo) &&
    (!ids || ids.has(c.call_id)));
}

// Nodos que toca un caso: su comercio, el comercio donde se comprometió la tarjeta (si el Agente 2
// detectó uno), su canal, la ciudad del comercio y su tipología
function nodosDelCaso(c, compromisos) {
  const nodos = [];
  if (c.terminal_id) nodos.push(["comercio", c.terminal_id, c.terminal_id]);
  (compromisos[c.call_id] || []).forEach(t => { if (t !== c.terminal_id) nodos.push(["comercio", t, t]); });
  if (c.canal_txn) nodos.push(["canal", c.canal_txn, NOMBRE_CANAL[c.canal_txn] || c.canal_txn]);
  if (c.ciudad_comercio) nodos.push(["ciudad", c.ciudad_comercio, c.ciudad_comercio]);
  const tipo = c.tipologia_analista || c.modalidad;
  if (tipo) nodos.push(["tipologia", tipo, nombreModalidad(tipo)]);
  return nodos.map(([grupo, valor, label]) => ({ id: `${grupo}:${valor}`, grupo, valor, label }));
}

function renderizarGrafo() {
  if (!AppState.data) return;
  const entidades = (AppState.data.embudo && AppState.data.embudo.entidades_detalle) || [];
  const sospechosos = Object.fromEntries(entidades.map(e => [e.entidad_id, e]));
  const compromisos = {};
  entidades.filter(e => e.tipo === "punto_de_compromiso").forEach(e =>
    (e.casos || []).forEach(id => (compromisos[id] = compromisos[id] || []).push(e.entidad_id)));

  const casos = casosDelGrafo();
  const nodos = {};
  casos.forEach(c => nodosDelCaso(c, compromisos).forEach(n => {
    const nodo = nodos[n.id] = nodos[n.id] || { ...n, casos: new Set(), monto: 0 };
    if (!nodo.casos.has(c.call_id)) { nodo.casos.add(c.call_id); nodo.monto += parseFloat(c.amount || 0); }
  }));
  const comercios = Object.values(nodos).filter(n => n.grupo === "comercio").sort((a, b) => b.casos.size - a.casos.size);
  const principal = comercios[0];
  document.getElementById("g-casos").textContent = casos.length.toLocaleString("es-PE");
  document.getElementById("g-principal").textContent = principal ? principal.label : "—";

  const svg = document.getElementById("g-svg");
  if (!casos.length) {
    svg.innerHTML = '<text x="500" y="320" text-anchor="middle" class="g-vacio">No hay casos con estos filtros.</text>';
    document.getElementById("g-nodos").textContent = document.getElementById("g-conexiones").textContent = "0";
    document.getElementById("g-detalle").innerHTML = '<p class="m-sec-text texto-tenue">Sin casos en la vista.</p>';
    return;
  }
  const centro = nodos[AppState.grafoCentro] || principal || Object.values(nodos)[0];
  AppState.grafoCentro = centro.id;

  // Alrededor: lo que comparte casos con el centro, los de más casos primero
  const anillo = [];
  Object.keys(GRUPOS_GRAFO).forEach(grupo => {
    const vecinos = Object.values(nodos)
      .filter(n => n.grupo === grupo && n.id !== centro.id)
      .map(n => ({ ...n, peso: [...n.casos].filter(id => centro.casos.has(id)).length }))
      .filter(n => n.peso > 0)
      .sort((a, b) => b.peso - a.peso);
    vecinos.slice(0, MAX_POR_GRUPO).forEach((n, k, arr) => anillo.push({ ...n, k, total: arr.length, ocultos: vecinos.length - arr.length }));
  });
  document.getElementById("g-nodos").textContent = anillo.length + 1;
  document.getElementById("g-conexiones").textContent = anillo.length;

  const cx = 500, cy = 320, rx = 360, ry = 250;
  const maxPeso = Math.max(...anillo.map(n => n.peso), 1);
  const posicion = n => {
    const [a, b] = GRUPOS_GRAFO[n.grupo].sector;
    const ang = (n.total === 1 ? (a + b) / 2 : a + (b - a) * n.k / (n.total - 1)) * Math.PI / 180;
    return { x: cx + rx * Math.cos(ang), y: cy - ry * Math.sin(ang), ang };
  };
  const iconoNodo = (nombre, x, y, escala) =>
    `<g transform="translate(${x - 12 * escala},${y - 12 * escala}) scale(${escala})" fill="none" stroke="#ffffff"
        stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" pointer-events="none">${SVG[nombre] || ""}</g>`;

  let lineas = "", circulos = "";
  anillo.forEach(n => {
    const g = GRUPOS_GRAFO[n.grupo], p = posicion(n);
    const ancho = (1.5 + 7 * n.peso / maxPeso).toFixed(1);
    const mx = (cx + p.x) / 2, my = (cy + p.y) / 2;
    lineas += `<line x1="${cx}" y1="${cy}" x2="${p.x.toFixed(1)}" y2="${p.y.toFixed(1)}" stroke="${g.color}" stroke-width="${ancho}"
        stroke-opacity="0.45" stroke-linecap="round"/><circle cx="${mx.toFixed(1)}" cy="${my.toFixed(1)}" r="5" fill="${g.color}" fill-opacity="0.75"/>`;
    // Etiqueta hacia afuera del nodo
    const cos = Math.cos(p.ang), sin = Math.sin(p.ang);
    const lateral = Math.abs(cos) > 0.45;
    const tx = lateral ? p.x + (cos > 0 ? 38 : -38) : p.x;
    const ty = lateral ? p.y - 2 : p.y + (sin > 0 ? -52 : 50);
    const anchor = lateral ? (cos > 0 ? "start" : "end") : "middle";
    const sosp = n.grupo === "comercio" && sospechosos[n.valor];
    circulos += `<g class="g-nodo" data-id="${esc(n.id)}" tabindex="0" role="button" aria-label="${esc(n.label)}, ${n.peso} casos">
      <title>${esc(g.nombre)}: ${esc(n.label)} · ${n.peso} casos compartidos con ${esc(centro.label)}</title>
      <circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="27" fill="${g.color}" ${sosp ? 'stroke="#dc2626" stroke-width="3"' : 'stroke="#ffffff" stroke-width="3"'}/>
      ${iconoNodo(g.icono, p.x, p.y, 1)}
      <text x="${tx.toFixed(1)}" y="${ty.toFixed(1)}" text-anchor="${anchor}" class="g-etq">${esc(n.label)}</text>
      <text x="${tx.toFixed(1)}" y="${(ty + 19).toFixed(1)}" text-anchor="${anchor}" class="g-sub">${n.peso} caso${n.peso === 1 ? "" : "s"}${sosp ? " · sospechoso" : ""}</text>
    </g>`;
  });
  // Grupos con más vecinos de los que se muestran
  let notas = "";
  Object.entries(GRUPOS_GRAFO).forEach(([grupo, g]) => {
    const n = anillo.find(x => x.grupo === grupo && x.ocultos > 0);
    if (!n) return;
    const ang = ((g.sector[0] + g.sector[1]) / 2) * Math.PI / 180;
    notas += `<text x="${(cx + (rx + 95) * Math.cos(ang)).toFixed(1)}" y="${(cy - (ry + 60) * Math.sin(ang)).toFixed(1)}"
      text-anchor="middle" class="g-mas">+${n.ocultos} ${g.plural.toLowerCase()} más</text>`;
  });
  const gc = GRUPOS_GRAFO[centro.grupo];
  const etiquetaCentro = `${centro.label} · ${centro.casos.size} caso${centro.casos.size === 1 ? "" : "s"}`;
  const anchoCaja = Math.max(170, etiquetaCentro.length * 9.6 + 32);
  svg.innerHTML = `${lineas}${notas}${circulos}
    <g class="g-centro">
      <circle cx="${cx}" cy="${cy}" r="52" fill="${gc.color}" fill-opacity="0.12"/>
      <circle cx="${cx}" cy="${cy}" r="38" fill="${gc.color}" stroke="#ffffff" stroke-width="4"/>
      ${iconoNodo(gc.icono, cx, cy, 1.3)}
      <rect x="${cx - anchoCaja / 2}" y="${cy + 60}" width="${anchoCaja}" height="36" rx="9" fill="#ffffff" stroke="#e2e8f0"/>
      <text x="${cx}" y="${cy + 84}" text-anchor="middle" class="g-etq-centro">${esc(etiquetaCentro)}</text>
    </g>`;
  svg.querySelectorAll(".g-nodo").forEach(el => {
    const centrar = () => { AppState.grafoCentro = el.dataset.id; renderizarGrafo(); };
    el.addEventListener("click", centrar);
    el.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); centrar(); } });
  });
  renderizarDetalleNodo(centro, casos, sospechosos[centro.valor] && centro.grupo === "comercio" ? sospechosos[centro.valor] : null);
}

function renderizarDetalleNodo(nodo, casosVista, entidad) {
  const g = GRUPOS_GRAFO[nodo.grupo];
  const casos = casosVista.filter(c => nodo.casos.has(c.call_id));
  const fechas = casos.map(c => String(c.fecha_llamada || "").slice(0, 10)).filter(Boolean).sort();
  const tipos = {};
  casos.forEach(c => { const t = c.tipologia_analista || c.modalidad; tipos[t] = (tipos[t] || 0) + 1; });
  const maxT = Math.max(...Object.values(tipos), 1);
  const patrones = AppState.patrones.map((p, i) => ({ p, i, n: (p.casos_asociados || []).filter(id => nodo.casos.has(id)).length }))
    .filter(x => x.n > 0).sort((a, b) => b.n - a.n);
  const tipoEnt = entidad ? (TIPOS_ENTIDAD[entidad.tipo] || { nombre: entidad.tipo, explica: "" }) : null;
  document.getElementById("g-detalle").innerHTML = `
    <div class="g-det-cabecera"><span class="g-det-ico" style="background:${g.color}">${icono(g.icono, 20)}</span>
      <div><span class="g-det-tipo">${esc(g.nombre)}</span><strong class="g-det-nombre">${esc(nodo.label)}</strong></div></div>
    <div class="g-det-cifras">
      <div><span>Casos</span><strong>${casos.length}</strong></div>
      <div><span>Monto</span><strong>${esc(formatoSoles(nodo.monto))}</strong></div>
      <div><span>Periodo</span><strong>${fechas.length ? `${esc(fechaDMY(fechas[0]))} – ${esc(fechaDMY(fechas[fechas.length - 1]))}` : "—"}</strong></div>
    </div>
    ${entidad ? `<div class="g-det-alerta"><strong>${esc(tipoEnt.nombre)}</strong> detectado por el Agente 2:
      ${esc(entidad.n_victimas ?? entidad.n_casos)} víctimas frente a ${esc(String(entidad.esperado_por_azar).replace(".", ","))} esperadas por casualidad.
      <span>${esc(tipoEnt.explica)}</span></div>` : ""}
    ${nodo.grupo !== "tipologia" ? `<h4 class="m-sec-title cp-sub">Tipologías</h4>
      ${Object.entries(tipos).sort((a, b) => b[1] - a[1]).map(([t, n]) => `
        <div class="g-det-barra"><span>${esc(nombreModalidad(t))}</span>
          <span class="peso-pista"><span class="peso-barra" style="width:${n / maxT * 100}%;background:${GRUPOS_GRAFO.tipologia.color}"></span></span><b>${n}</b></div>`).join("")}` : ""}
    <h4 class="m-sec-title cp-sub">Patrones que lo incluyen</h4>
    ${patrones.length ? patrones.map(x => `<button class="chip-patron g-det-patron" data-patron="${x.p.patron_id}">
        P${String(x.i + 1).padStart(2, "0")} · ${esc(x.p.titulo.slice(0, 60))}${x.p.titulo.length > 60 ? "…" : ""} <b>(${x.n})</b></button>`).join("")
      : '<p class="m-sec-text texto-tenue">Ningún patrón vigente incluye estos casos.</p>'}
    <h4 class="m-sec-title cp-sub">Casos</h4>
    <div class="g-det-casos">${casos.slice(0, 10).map(c =>
      `<button class="patron-id-chip g-det-caso" data-caso="${c.call_id}" title="${esc(nombreModalidad(c.tipologia_analista || c.modalidad))} · ${esc(formatoSoles(c.amount))}">${codigoCaso(c.call_id)}</button>`).join("")}
      ${casos.length > 10 ? `<span class="texto-tenue">y ${casos.length - 10} más</span>` : ""}</div>
    <button class="btn-secondary g-det-ver" id="g-ver-casos">Ver estos casos en la lista →</button>`;
  const cont = document.getElementById("g-detalle");
  cont.querySelectorAll("[data-patron]").forEach(b => b.addEventListener("click", () => {
    const p = AppState.patrones.find(x => String(x.patron_id) === b.dataset.patron);
    if (p) abrirModalDetalle(p);
  }));
  cont.querySelectorAll("[data-caso]").forEach(b => b.addEventListener("click", () => irACaso(parseInt(b.dataset.caso, 10))));
  document.getElementById("g-ver-casos").addEventListener("click", () => {
    document.getElementById("casos-buscar").value = nodo.grupo === "tipologia" ? "" : nodo.valor;
    document.getElementById("casos-filtro-modalidad").value = nodo.grupo === "tipologia" ? nodo.valor : "";
    mostrarVista("reclamos");
    renderizarTablaCasos();
  });
}
