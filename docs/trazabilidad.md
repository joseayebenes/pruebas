# Matriz de trazabilidad

Responde a la pregunta "¿donde esta implementado esto y que lo verifica?". Los requisitos
vienen de [`especificacion.md`](especificacion.md).

**Estados**: `Implementado` = hay codigo y test · `Manual` = implementado, se verifica con
DOORS real · `Planificado` = hito posterior, sin codigo.

Alcance de esta entrega: hitos H0 a H3. Todo lo de H4 en adelante figura como planificado.

## 3.1 Acceso a DOORS y gestion de sesion

| ID | Modulo | Verificacion | Estado |
|---|---|---|---|
| RF-001 | `sources/doors/client.py` | `test_doors_client.py` (COM simulado) | Manual |
| RF-002 | `sources/doors/client.py::_crear_sesion` | Revisado: usa `Dispatch`, no `GetActiveObject` | Manual |
| RF-003 | `sources/doors/client.py::start_session` | `test_doors_client.py::test_consultar_sin_sesion_dice_que_hacer` | Manual |
| RF-004 | `sources/doors/dxl.py::_abrir_modulo` | `test_dxl_generation.py::test_los_modulos_se_abren_siempre_en_lectura` | Implementado |
| RF-005 | `config.py::resolve_module_path` | `test_config.py`, `test_cli.py` | Implementado |
| RF-006 | `servers/doors_server.py` (`module_path` opcional) | `test_config.py::test_resolve_module_path_prefiere_el_modulo_explicito` | Implementado |
| RF-007 | `sources/doors/client.py::status` | `test_doors_client.py` | Implementado |

## 3.2 Esquema y atributos

| ID | Modulo | Verificacion | Estado |
|---|---|---|---|
| RF-010 | `dxl.py::script_list_attributes`, `client.py::list_object_attributes` | `test_doors_client.py` | Implementado |
| RF-011 | `models.py::AttributeDefinition` | `test_doors_client.py::test_los_atributos_se_parsean_con_sus_metadatos` | Implementado |
| RF-012 | `sources/base.py::validar_nombres` | `test_fake_source.py`, `test_doors_client.py`, `test_sync_safety.py` | Implementado |
| RF-013 | `sources/base.py::sugerir_nombres`, `errors.py::AttributeValidationError` | `test_mcp_tools.py::test_un_atributo_mal_escrito_devuelve_un_error_con_sugerencias` | Implementado |
| RF-014 | `config.py::DEFAULT_CONTENT_ATTRIBUTES`, `client.py::_a_registro` | `test_config.py`, `test_doors_client.py` | Implementado |

## 3.3 Consulta de requisitos

| ID | Modulo | Verificacion | Estado |
|---|---|---|---|
| RF-020 | `dxl.py::script_fetch_page`, tool `list_requirements` | `test_mcp_tools.py::test_listar_requisitos_devuelve_cursor_para_continuar` | Implementado |
| RF-021 | `client.py::get_requirement`, tool `get_requirement` | `test_mcp_tools.py::test_un_requisito_incluye_los_campos_minimos` | Implementado |
| RF-022 | — | Pendiente: la version actual siempre recorre el modulo completo | Planificado |
| RF-023 | `dxl.py::_filtros_de_objeto` | `test_dxl_generation.py`, `test_fake_source.py` | Implementado |
| RF-024 | `dxl.py::_filtros_de_objeto` | `test_fake_source.py::test_las_filas_internas_de_tabla_se_excluyen_por_defecto` | Implementado |
| RF-025 | `models.py::RequirementRecord.to_dict` | `test_mcp_tools.py::test_un_requisito_incluye_los_campos_minimos` | Implementado |

## 3.4 Busqueda y trazabilidad

| ID | Modulo | Verificacion | Estado |
|---|---|---|---|
| RF-030 | `dxl.py::script_search` | `test_dxl_generation.py`, `test_fake_source.py` | Implementado |
| RF-031 | tool `search_requirements` (`attributes` por defecto) | `test_mcp_tools.py` | Implementado |
| RF-032 | `dxl.py::script_search` (`case_sensitive`) | `test_dxl_generation.py::test_la_busqueda_distingue_mayusculas_solo_cuando_se_pide` | Implementado |
| RF-033 | `dxl.py::script_search` (`regex`) | `test_dxl_generation.py`, `test_fake_source.py` | Implementado |
| RF-034 | `models.py::SearchHit` | `test_mcp_tools.py::test_la_busqueda_indica_donde_encontro_la_coincidencia` | Implementado |
| RF-035 | `dxl.py::script_get_links` | `test_dxl_generation.py::test_la_direccion_de_la_trazabilidad_filtra_los_bloques_generados` | Implementado |
| RF-036 | `dxl.py::script_get_links` (`load_failures`) | `test_dxl_generation.py` | Manual |
| RF-037 | tool `get_requirement_links` (`oslc_links_included: false`) | `test_mcp_tools.py::test_la_trazabilidad_declara_que_no_cubre_oslc` | Implementado |

## 3.5 Seguridad y control de respuestas

| ID | Modulo | Verificacion | Estado |
|---|---|---|---|
| RF-040 | `servers/doors_server.py` (catalogo cerrado) | `test_mcp_tools.py::test_no_existe_ninguna_herramienta_de_dxl_arbitrario` | Implementado |
| RF-041 | `dxl.py::escape_dxl_string`, `literal_json` | `test_dxl_generation.py` (inyeccion por ruta, termino y atributo) | Implementado |
| RF-042 | `servers/doors_server.py::SOLO_LECTURA` | `test_mcp_tools.py::test_todas_las_herramientas_se_declaran_de_solo_lectura` | Implementado |
| RF-043 | `servers/response.py::serializar` | `test_mcp_tools.py::test_el_limite_duro_de_la_configuracion_se_aplica_de_verdad` | Implementado |
| RF-044 | `servers/response.py::serializar` | `test_mcp_tools.py` (recorte anunciado y error de tamano) | Implementado |

## 3.6 Copia local SQLite y sincronizacion

| ID | Modulo | Verificacion | Estado |
|---|---|---|---|
| RF-050 | `db/repository.py` | `test_repository.py` | Implementado |
| RF-051 | `db/schema.sql` | `test_cli.py` (esquema creado de extremo a extremo) | Implementado |
| RF-052 | `db/schema.sql` (`UNIQUE(module_path, absolute_number)`) | `test_repository.py` | Implementado |
| RF-053 | tabla `requirement_attributes` | `test_repository.py::test_los_atributos_se_guardan_normalizados` | Implementado |
| RF-054 | `models.py::RequirementRecord.content_hash` | `test_models.py` | Implementado |
| RF-055 | `db/repository.py::upsert_requirement` | `test_repository.py`, `test_sync_fake.py` | Implementado |
| RF-056 | `db/repository.py::upsert_requirement` (reactivacion) | `test_repository.py::test_un_requisito_borrado_que_reaparece_se_reactiva_aunque_el_hash_coincida` | Implementado |
| RF-057 | `sync/service.py::_recorrer_modulo` | `test_sync_pagination.py` | Implementado |
| RF-058 | `dxl.py::_posicionar_cursor`, `models.py::RequirementPage` | `test_dxl_generation.py`, `test_fake_source.py` | Implementado |
| RF-059 | `config.py` (`sync_page_size`, `max_attribute_chars`) | `test_config.py`, `test_cli.py` | Implementado |
| RF-060 | `sync/service.py::sync_module` (paso 1) | `test_sync_safety.py::test_un_atributo_inexistente_se_rechaza_antes_de_extraer_nada` | Implementado |
| RF-061 | `sync/service.py` + `repository.mark_missing_as_deleted` | `test_sync_safety.py::test_un_corte_a_mitad_no_marca_nada_como_eliminado` | Implementado |
| RF-062 | tabla `sync_runs`, `repository.finish_sync_run` | `test_repository.py`, `test_sync_safety.py` | Implementado |
| RF-063 | `sync/service.py::_aplicar_fecha_de_origen` | `test_sync_fake.py::test_el_atributo_de_fecha_configurado_no_entra_en_el_hash` | Implementado |
| RF-064 | `config.py` (`DOORS_DB_PATH`) | `test_cli.py` | Implementado |

## 3.7 Busqueda local, embeddings y RAG (hitos H4-H8)

RF-070 a RF-080: **Planificado**. Sin codigo en esta entrega. El esquema de
[`schema.sql`](../src/doors_kb/db/schema.sql) deja sitio a la tabla FTS5 y a las de
embeddings sin migraciones destructivas, y la clasificacion `inserted`/`updated`/`unchanged`
que ya produce el sincronizador es la senal que RF-074 necesitara. La eleccion de proveedor
de embeddings esta decidida en [ADR-009](adr.md).

## 4. Requisitos no funcionales

| ID | Modulo | Verificacion | Estado |
|---|---|---|---|
| RNF-001 | `sources/doors/` | Import perezoso de pywin32; extra `[win]` | Manual |
| RNF-002 | `com_worker.py::ComWorker` | `test_com_worker.py::test_todas_las_llamadas_se_ejecutan_en_el_mismo_hilo` | Implementado |
| RNF-003 | `config.py`, `client.py::start_session` | `test_config.py` | Implementado |
| RNF-004 | `com_worker.py::call` | `test_com_worker.py::test_un_timeout_marca_el_worker_como_bloqueado` | Implementado |
| RNF-005 | `com_worker.py` (envenenamiento) | `test_com_worker.py::test_tras_un_timeout_toda_llamada_posterior_falla` | Implementado |
| RNF-006 | `com_worker.py::_ejecutar_con_reintentos` | `test_com_worker.py::test_los_errores_de_doors_ocupado_se_reintentan` | Implementado |
| RNF-007 | `dxl.py::build_preamble` | `test_dxl_generation.py::test_el_watchdog_de_dxl_es_configurable` | Implementado |
| RNF-008 | `dxl.py::build_preamble` | `test_dxl_generation.py::test_el_preambulo_termina_en_un_salto_de_linea_real` | Implementado |
| RNF-009 | `servers/doors_server.py::configurar_logging` | Revisado: `logging` a `sys.stderr`, sin `print` a stdout | Implementado |
| RNF-010 | `config.py`, `servers/response.py` | `test_mcp_tools.py` | Implementado |
| RNF-011 | `sources/base.py::truncar`, `dxl.py` (`cut`) | `test_fake_source.py::test_el_truncado_deja_marca_visible` | Implementado |
| RNF-012 | `db/repository.py` (WAL, `transaction()`) | `test_repository.py::test_una_transaccion_fallida_no_deja_escrituras_a_medias` | Implementado |
| RNF-013 | `sources/base.py`, `sources/fake.py` | Toda la suite corre sin DOORS ni Windows | Implementado |
| RNF-014 | Estructura del paquete | `docs/arquitectura.md` | Implementado |
| RNF-015 | `sync/service.py`, `dxl.py::_posicionar_cursor` | `test_sync_pagination.py::test_cada_objeto_se_visita_una_sola_vez` | Implementado |
| RNF-016 | — | Reconstruccion de indices derivados: sin indices aun | Planificado |
| RNF-017 | Catalogo cerrado + `read(...)` | `test_mcp_tools.py`, `test_dxl_generation.py` | Implementado |

## Criterios de aceptacion del hito de sincronizacion

| ID | Verificacion | Estado |
|---|---|---|
| CA-001 | Requiere un modulo real: ver [`operacion_windows.md`](operacion_windows.md) | Manual |
| CA-002 | `test_sync_fake.py::test_dos_sincronizaciones_seguidas_sin_cambios`, `test_cli.py` | Implementado |
| CA-003 | `test_sync_fake.py::test_una_modificacion_produce_un_solo_updated` | Implementado |
| CA-004 | `test_sync_fake.py::test_un_alta_y_una_baja_en_la_misma_pasada` | Implementado |
| CA-005 | `test_sync_safety.py::test_un_corte_a_mitad_no_marca_nada_como_eliminado` | Implementado |
| CA-006 | `test_sync_pagination.py::test_cada_objeto_se_visita_una_sola_vez` (coste lineal) | Implementado |
| CA-007 | `test_sync_safety.py::test_un_atributo_inexistente_se_rechaza_antes_de_extraer_nada` | Implementado |

## Riesgos conocidos y su estado

| ID | Estado en esta entrega |
|---|---|
| R-001 | Mitigado: timeout de sesion, deteccion de worker bloqueado y guia de login |
| R-002 | Mitigado en parte: los modulos origen que no cargan se reportan en `load_failures` |
| R-003 | Aceptado: la trazabilidad declara `oslc_links_included: false` |
| R-004 | **Cerrado**: el MCP directo nace con cursor, no con offset |
| R-005 | Abierto: el perfil de atributos define el hash; conviene fijarlo antes de H5 |
| R-006 | Pendiente de H5 (decidido en ADR-009) |
| R-007 | Mitigado: `module_freshness` distingue sincronizacion parcial de completa |
