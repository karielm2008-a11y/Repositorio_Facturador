import ssl
import xml.etree.ElementTree as ET

import requests
import streamlit as st
import streamlit.components.v1 as components
from jinja2 import Template
from requests.adapters import HTTPAdapter
from zeep import Client
from zeep.helpers import serialize_object
from zeep.transports import Transport


WSDL_PRODUCCION = "https://cel.sri.gob.ec/comprobantes-electronicos-ws/AutorizacionComprobantesOffline?wsdl"


class TLSAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def texto(nodo, etiqueta):
    if nodo is None:
        return ""
    e = nodo.find(etiqueta)
    return e.text.strip() if e is not None and e.text else ""


def consultar_sri(clave_acceso):
    session = requests.Session()
    session.mount("https://", TLSAdapter())
    session.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Connection": "close"
    })

    client = Client(
        wsdl=WSDL_PRODUCCION,
        transport=Transport(session=session, timeout=60)
    )

    respuesta = client.service.autorizacionComprobante(
        claveAccesoComprobante=clave_acceso
    )

    data = serialize_object(respuesta)
    autorizaciones_data = data.get("autorizaciones") if data else None

    if not autorizaciones_data:
        raise Exception("El SRI no devolvió autorizaciones para esta clave.")

    autorizaciones = autorizaciones_data.get("autorizacion", [])

    if isinstance(autorizaciones, dict):
        autorizaciones = [autorizaciones]

    if not autorizaciones:
        raise Exception("El SRI no devolvió autorizaciones para esta clave.")

    aut = autorizaciones[0]

    return {
        "estado": aut.get("estado"),
        "numero_autorizacion": aut.get("numeroAutorizacion"),
        "fecha_autorizacion": aut.get("fechaAutorizacion"),
        "ambiente": str(aut.get("ambiente")).replace("�", "Ó"),
        "comprobante_xml": aut.get("comprobante")
    }


def detectar_tipo_comprobante(info_tributaria):
    cod_doc = texto(info_tributaria, "codDoc")

    tipos = {
        "01": "FACTURA",
        "04": "NOTA DE CRÉDITO",
        "05": "NOTA DE DÉBITO",
        "06": "GUÍA DE REMISIÓN",
        "07": "COMPROBANTE DE RETENCIÓN"
    }

    return cod_doc, tipos.get(cod_doc, "OTRO COMPROBANTE")


def parsear_xml(xml):
    root = ET.fromstring(xml)

    info_tributaria = root.find("infoTributaria")
    cod_doc, tipo_comprobante = detectar_tipo_comprobante(info_tributaria)

    info_factura = root.find("infoFactura")
    info_nota_credito = root.find("infoNotaCredito")
    info_nota_debito = root.find("infoNotaDebito")

    if cod_doc == "01":
        info_principal = info_factura
    elif cod_doc == "04":
        info_principal = info_nota_credito
    elif cod_doc == "05":
        info_principal = info_nota_debito
    else:
        info_principal = info_factura or info_nota_credito or info_nota_debito

    detalles = root.find("detalles")
    info_adicional = root.find("infoAdicional")

    datos = {
        "tipo_comprobante": tipo_comprobante,
        "cod_doc": cod_doc,
        "ruc_emisor": texto(info_tributaria, "ruc"),
        "razon_social": texto(info_tributaria, "razonSocial"),
        "nombre_comercial": texto(info_tributaria, "nombreComercial"),
        "dir_matriz": texto(info_tributaria, "dirMatriz"),
        "establecimiento": texto(info_tributaria, "estab"),
        "punto_emision": texto(info_tributaria, "ptoEmi"),
        "secuencial": texto(info_tributaria, "secuencial"),
        "clave_acceso": texto(info_tributaria, "claveAcceso"),
        "fecha_emision": texto(info_principal, "fechaEmision"),
        "dir_establecimiento": texto(info_principal, "dirEstablecimiento"),
        "obligado_contabilidad": texto(info_principal, "obligadoContabilidad"),
        "identificacion_comprador": texto(info_principal, "identificacionComprador"),
        "razon_social_comprador": texto(info_principal, "razonSocialComprador"),
        "direccion_comprador": texto(info_principal, "direccionComprador"),
        "total_sin_impuestos": texto(info_principal, "totalSinImpuestos"),
        "total_descuento": texto(info_principal, "totalDescuento"),
        "importe_total": texto(info_principal, "importeTotal") or texto(info_principal, "valorModificacion"),
        "moneda": texto(info_principal, "moneda"),
        "cod_doc_modificado": texto(info_principal, "codDocModificado"),
        "num_doc_modificado": texto(info_principal, "numDocModificado"),
        "fecha_doc_sustento": texto(info_principal, "fechaEmisionDocSustento"),
        "motivo": texto(info_principal, "motivo"),
        "valor_modificacion": texto(info_principal, "valorModificacion"),
        "productos": [],
        "impuestos": [],
        "pagos": [],
        "adicionales": []
    }

    if detalles is not None:
        for d in detalles.findall("detalle"):
            datos["productos"].append({
                "codigo_principal": texto(d, "codigoPrincipal"),
                "codigo_auxiliar": texto(d, "codigoAuxiliar"),
                "descripcion": texto(d, "descripcion"),
                "cantidad": texto(d, "cantidad"),
                "precio_unitario": texto(d, "precioUnitario"),
                "descuento": texto(d, "descuento"),
                "precio_total": texto(d, "precioTotalSinImpuesto")
            })

    total_con_impuestos = info_principal.find("totalConImpuestos") if info_principal is not None else None
    if total_con_impuestos is not None:
        for imp in total_con_impuestos.findall("totalImpuesto"):
            datos["impuestos"].append({
                "codigo": texto(imp, "codigo"),
                "codigo_porcentaje": texto(imp, "codigoPorcentaje"),
                "base_imponible": texto(imp, "baseImponible"),
                "valor": texto(imp, "valor")
            })

    pagos = info_principal.find("pagos") if info_principal is not None else None
    if pagos is not None:
        for p in pagos.findall("pago"):
            datos["pagos"].append({
                "forma_pago": texto(p, "formaPago"),
                "total": texto(p, "total"),
                "plazo": texto(p, "plazo"),
                "unidad_tiempo": texto(p, "unidadTiempo")
            })

    if info_adicional is not None:
        for campo in info_adicional.findall("campoAdicional"):
            datos["adicionales"].append({
                "nombre": campo.attrib.get("nombre", ""),
                "valor": campo.text or ""
            })

    return datos


def generar_html(datos, autorizacion):
    template = Template("""
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>{{ datos.tipo_comprobante }} SRI {{ datos.establecimiento }}-{{ datos.punto_emision }}-{{ datos.secuencial }}</title>
<style>
    body { font-family: Arial, sans-serif; margin: 25px; color: #111; background: #fff; }
    .contenedor { max-width: 1000px; margin: auto; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
    .box { border: 1px solid #222; border-radius: 8px; padding: 12px; margin-bottom: 12px; }
    .titulo { font-size: 24px; font-weight: bold; margin-bottom: 6px; }
    .subtitulo { font-size: 18px; font-weight: bold; }
    .linea { margin: 5px 0; word-break: break-word; }
    table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13px; }
    th, td { border: 1px solid #333; padding: 6px; vertical-align: top; }
    th { background: #efefef; }
    .right { text-align: right; }
    .estado {
        font-size: 15px;
        font-weight: bold;
        padding: 7px 12px;
        border-radius: 16px;
        background: #16a34a;
        color: white;
        display: inline-block;
    }
    .alerta {
        background: #fff7ed;
        border: 1px solid #fb923c;
        border-radius: 8px;
        padding: 10px;
        margin-top: 10px;
    }
    .footer { margin-top: 20px; font-size: 12px; color: #444; }
</style>
</head>
<body>
<div class="contenedor">

    <div class="grid">
        <div class="box">
            <div class="subtitulo">{{ datos.razon_social }}</div>
            <div class="linea"><b>Nombre comercial:</b> {{ datos.nombre_comercial }}</div>
            <div class="linea"><b>RUC:</b> {{ datos.ruc_emisor }}</div>
            <div class="linea"><b>Dir. Matriz:</b> {{ datos.dir_matriz }}</div>
            <div class="linea"><b>Dir. Sucursal:</b> {{ datos.dir_establecimiento }}</div>
            <div class="linea"><b>Obligado a llevar contabilidad:</b> {{ datos.obligado_contabilidad }}</div>
        </div>

        <div class="box">
            <div class="titulo">{{ datos.tipo_comprobante }}</div>
            <div class="linea"><b>No.:</b> {{ datos.establecimiento }}-{{ datos.punto_emision }}-{{ datos.secuencial }}</div>
            <div class="linea"><b>Estado SRI:</b> <span class="estado">{{ autorizacion.estado }}</span></div>
            <div class="linea"><b>Ambiente:</b> {{ autorizacion.ambiente }}</div>
            <div class="linea"><b>Número de autorización:</b> {{ autorizacion.numero_autorizacion }}</div>
            <div class="linea"><b>Fecha autorización:</b> {{ autorizacion.fecha_autorizacion }}</div>
            <div class="linea"><b>Clave de acceso:</b> {{ datos.clave_acceso }}</div>

            {% if datos.tipo_comprobante == "NOTA DE CRÉDITO" %}
            <div class="alerta">
                <div class="linea"><b>Documento modificado:</b> {{ datos.num_doc_modificado }}</div>
                <div class="linea"><b>Código doc. modificado:</b> {{ datos.cod_doc_modificado }}</div>
                <div class="linea"><b>Fecha doc. sustento:</b> {{ datos.fecha_doc_sustento }}</div>
                <div class="linea"><b>Motivo:</b> {{ datos.motivo }}</div>
                <div class="linea"><b>Valor modificación:</b> {{ datos.valor_modificacion }}</div>
            </div>
            {% endif %}
        </div>
    </div>

    <div class="box">
        <div class="subtitulo">Datos del comprador</div>
        <div class="linea"><b>Razón social / nombres:</b> {{ datos.razon_social_comprador }}</div>
        <div class="linea"><b>RUC / CI:</b> {{ datos.identificacion_comprador }}</div>
        <div class="linea"><b>Fecha emisión:</b> {{ datos.fecha_emision }}</div>
        <div class="linea"><b>Dirección:</b> {{ datos.direccion_comprador }}</div>
    </div>

    <div class="box">
        <div class="subtitulo">Detalle de productos / servicios</div>
        <table>
            <thead>
                <tr>
                    <th>Cód. Principal</th>
                    <th>Cód. Auxiliar</th>
                    <th>Cant.</th>
                    <th>Descripción</th>
                    <th>Precio Unitario</th>
                    <th>Descuento</th>
                    <th>Precio Total</th>
                </tr>
            </thead>
            <tbody>
                {% for p in datos.productos %}
                <tr>
                    <td>{{ p.codigo_principal }}</td>
                    <td>{{ p.codigo_auxiliar }}</td>
                    <td class="right">{{ p.cantidad }}</td>
                    <td>{{ p.descripcion }}</td>
                    <td class="right">{{ p.precio_unitario }}</td>
                    <td class="right">{{ p.descuento }}</td>
                    <td class="right">{{ p.precio_total }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>

    <div class="grid">
        <div class="box">
            <div class="subtitulo">Información adicional</div>
            <table>
                {% for a in datos.adicionales %}
                <tr>
                    <td><b>{{ a.nombre }}</b></td>
                    <td>{{ a.valor }}</td>
                </tr>
                {% endfor %}
            </table>
        </div>

        <div class="box">
            <div class="subtitulo">Desglose de valores</div>
            <table>
                <tr><td>Subtotal sin impuestos</td><td class="right">{{ datos.total_sin_impuestos }}</td></tr>
                <tr><td>Total descuento</td><td class="right">{{ datos.total_descuento }}</td></tr>

                {% for i in datos.impuestos %}
                <tr>
                    <td>Impuesto código {{ i.codigo }} / porcentaje {{ i.codigo_porcentaje }}<br>Base: {{ i.base_imponible }}</td>
                    <td class="right">{{ i.valor }}</td>
                </tr>
                {% endfor %}

                {% if datos.valor_modificacion %}
                <tr><td>Valor modificación</td><td class="right">{{ datos.valor_modificacion }}</td></tr>
                {% endif %}

                <tr><th>Valor total</th><th class="right">{{ datos.importe_total }}</th></tr>
            </table>

            <div class="subtitulo" style="margin-top:15px;">Forma de pago</div>
            <table>
                <tr>
                    <th>Forma pago</th>
                    <th>Valor</th>
                    <th>Plazo</th>
                    <th>Tiempo</th>
                </tr>
                {% for p in datos.pagos %}
                <tr>
                    <td>{{ p.forma_pago }}</td>
                    <td class="right">{{ p.total }}</td>
                    <td>{{ p.plazo }}</td>
                    <td>{{ p.unidad_tiempo }}</td>
                </tr>
                {% endfor %}
            </table>
        </div>
    </div>

    <div class="footer">
        Vista generada automáticamente desde XML autorizado por el SRI. No incluye logo ni elementos gráficos comerciales.
    </div>

</div>
</body>
</html>
""")

    return template.render(datos=datos, autorizacion=autorizacion)


# =========================
# APP STREAMLIT
# =========================

st.set_page_config(
    page_title="Validador SRI",
    page_icon="🧾",
    layout="wide"
)

st.markdown("""
<style>
.block-container {
    padding-top: 2rem;
    max-width: 1200px;
}
.hero {
    background: linear-gradient(135deg, #0f172a 0%, #1e293b 55%, #0f766e 100%);
    padding: 34px;
    border-radius: 24px;
    margin-bottom: 25px;
    box-shadow: 0 12px 35px rgba(0,0,0,0.25);
}
.hero h1 {
    color: white;
    font-size: 44px;
    margin-bottom: 8px;
}
.hero p {
    color: #dbeafe;
    font-size: 17px;
}
.creator {
    color: white;
    font-weight: 700;
}
.ok-box {
    background: #064e3b;
    border-left: 5px solid #22c55e;
    padding: 16px 18px;
    border-radius: 12px;
    color: #dcfce7;
    margin: 18px 0;
}
.footer-app {
    text-align: center;
    color: #9ca3af;
    margin-top: 50px;
    font-size: 14px;
}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="hero">
    <h1>🧾 Validador de Facturas SRI</h1>
    <p>Consulta comprobantes electrónicos autorizados por el SRI 🧐.</p>
    <p class="creator">Creado por Kevin Muñoz A.</p>
</div>
""", unsafe_allow_html=True)

clave = st.text_input(
    "Ingrese clave de acceso / número de autorización",
    max_chars=49,
    placeholder="Pegue aquí la clave de acceso de 49 dígitos"
)

if st.button("🔍 Consultar comprobante", use_container_width=True):
    if len(clave) != 49 or not clave.isdigit():
        st.error("La clave debe tener exactamente 49 dígitos numéricos.")
    else:
        with st.spinner("Consultando información en el SRI..."):
            try:
                autorizacion = consultar_sri(clave)

                if autorizacion["estado"] != "AUTORIZADO":
                    st.warning(f"Estado devuelto por el SRI: {autorizacion['estado']}")
                    st.stop()

                xml = autorizacion["comprobante_xml"]

                if not xml:
                    st.error("El comprobante está autorizado, pero el SRI no devolvió XML.")
                    st.stop()

                datos = parsear_xml(xml)
                html_factura = generar_html(datos, autorizacion)

                nombre_base = f"{datos['tipo_comprobante'].replace(' ', '_')}_{datos['establecimiento']}-{datos['punto_emision']}-{datos['secuencial']}"

                st.markdown("""
                <div class="ok-box">
                    ✅ Comprobante autorizado y validado correctamente.
                </div>
                """, unsafe_allow_html=True)

                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Estado SRI", autorizacion["estado"])
                c2.metric("Tipo", datos["tipo_comprobante"])
                c3.metric("No.", f"{datos['establecimiento']}-{datos['punto_emision']}-{datos['secuencial']}")
                c4.metric("Total", datos["importe_total"])

                st.markdown("### Datos principales")

                col1, col2 = st.columns(2)

                with col1:
                    st.write(f"**Emisor:** {datos['razon_social']}")
                    st.write(f"**RUC emisor:** {datos['ruc_emisor']}")
                    st.write(f"**Nombre comercial:** {datos['nombre_comercial']}")
                    st.write(f"**Fecha emisión:** {datos['fecha_emision']}")

                with col2:
                    st.write(f"**Comprador:** {datos['razon_social_comprador']}")
                    st.write(f"**CI/RUC comprador:** {datos['identificacion_comprador']}")
                    st.write(f"**Ambiente:** {autorizacion['ambiente']}")
                    st.write(f"**Fecha autorización:** {autorizacion['fecha_autorizacion']}")

                if datos["tipo_comprobante"] == "NOTA DE CRÉDITO":
                    st.warning(
                        f"Nota de crédito asociada al documento {datos['num_doc_modificado']} "
                        f"por valor de {datos['valor_modificacion']}. Motivo: {datos['motivo']}"
                    )

                st.markdown("### Descargas")

                d1, d2 = st.columns(2)

                with d1:
                    st.download_button(
                        "⬇️ Descargar XML",
                        data=xml,
                        file_name=f"{nombre_base}.xml",
                        mime="application/xml",
                        use_container_width=True
                    )

                with d2:
                    st.download_button(
                        "⬇️ Descargar vista HTML",
                        data=html_factura,
                        file_name=f"{nombre_base}.html",
                        mime="text/html",
                        use_container_width=True
                    )

                st.markdown("### Vista del comprobante")
                components.html(html_factura, height=950, scrolling=True)

            except Exception as e:
                st.error(f"Error al consultar el SRI: {e}")

st.markdown("""
<div class="footer-app">
    Desarrollado por <b>Kevin Muñoz A.</b><br>
    Herramienta de validación documental.
</div>
""", unsafe_allow_html=True)
