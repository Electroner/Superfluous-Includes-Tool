import os
import re
import dash
import dash_cytoscape as cyto
from dash import html, dcc, Input, Output, State, ctx
from collections import defaultdict
import tempfile
import shutil

def scan_directory(directory):
    file_includes = {}
    c_extensions = ['.c', '.h', '.cpp', '.hpp', '.cc']

    for root, _, files in os.walk(directory):
        for file in files:
            if any(file.endswith(ext) for ext in c_extensions):
                path = os.path.join(root, file)
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        includes = re.findall(r'#include\s+[<"]([^>"]+)[>"]', content)
                        file_includes[path] = includes
                except Exception as e:
                    print(f"Error al leer {path}: {e}")
    
    return file_includes

def build_elements(file_includes):
    nodes = {}
    edges = []
    redundancy_info = {}  # Para almacenar información sobre redundancias
    redundant_includes = []  # Lista para almacenar información de includes redundantes

    # Crear nodos y aristas básicas
    for src_path, includes in file_includes.items():
        src = os.path.basename(src_path)
        if src not in nodes:
            nodes[src] = {'data': {'id': src, 'label': src}}

        for inc in includes:
            tgt = os.path.basename(inc)
            if tgt not in nodes:
                nodes[tgt] = {'data': {'id': tgt, 'label': tgt}}
            edges.append({'data': {'source': src, 'target': tgt, 'id': f"{src}__{tgt}", 'full_src_path': src_path, 'include': inc}})

    # Construir grafo de dependencias directas
    direct_deps = defaultdict(set)
    for edge in edges:
        src = edge['data']['source']
        tgt = edge['data']['target']
        direct_deps[src].add(tgt)

    # Calcular dependencias transitivas (indirectas)
    transitive_deps = defaultdict(set)
    
    def dfs(node, visited=None, path=None):
        if visited is None:
            visited = set()
        if path is None:
            path = []
            
        visited.add(node)
        path.append(node)
        
        for neighbor in direct_deps[node]:
            if neighbor not in visited:
                transitive_deps[path[0]].add(neighbor)
                dfs(neighbor, visited, path)
            else:
                transitive_deps[path[0]].add(neighbor)
                
        path.pop()
    
    # Ejecutar DFS para cada nodo
    for node in nodes:
        dfs(node)

    # Identificar inclusiones redundantes
    redundant_edges = []
    for edge in edges:
        src = edge['data']['source']
        tgt = edge['data']['target']
        
        # Verificar si hay un camino indirecto
        indirect_path_exists = False
        
        # Verificar si el destino es alcanzable a través de otras dependencias directas
        for intermediate in direct_deps[src] - {tgt}:
            if tgt in transitive_deps[intermediate]:
                indirect_path_exists = True
                # Guardar información sobre la redundancia
                if (src, tgt) not in redundancy_info:
                    redundancy_info[(src, tgt)] = []
                redundancy_info[(src, tgt)].append(intermediate)
        
        if indirect_path_exists:
            redundant_edges.append((src, tgt))
            edge['classes'] = 'redundant'
            
            # Añadir información de redundancia a los datos de la arista
            paths = redundancy_info.get((src, tgt), [])
            path_info = ', '.join(paths)
            edge['data']['tooltip'] = f"Redundante: {tgt} ya está incluido a través de {path_info}"
            
            # Guardar información para eliminar el include redundante
            redundant_includes.append({
                'file_path': edge['data']['full_src_path'],
                'include': edge['data']['include'],
                'reason': f"Ya incluido a través de {path_info}"
            })

    return list(nodes.values()) + edges, redundant_includes

def remove_redundant_includes(redundant_includes):
    """Elimina los includes redundantes de los archivos fuente"""
    modified_files = []
    
    for item in redundant_includes:
        file_path = item['file_path']
        include_to_remove = item['include']
        
        try:
            # Leer el contenido del archivo
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # Crear un archivo temporal para escribir el contenido modificado
            with tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8') as temp:
                removed = False
                for line in lines:
                    # Buscar la línea de include que queremos eliminar
                    include_pattern = f'#include\\s+[<"]({include_to_remove})[>"]'
                    if re.search(include_pattern, line):
                        # No escribir esta línea (eliminarla)
                        if not removed:  # Solo contar una vez si hay múltiples coincidencias
                            removed = True
                    else:
                        # Mantener todas las demás líneas
                        temp.write(line)
            
            # Reemplazar el archivo original con el modificado
            shutil.move(temp.name, file_path)
            
            if removed:
                modified_files.append(file_path)
                
        except Exception as e:
            print(f"Error al modificar {file_path}: {e}")
    
    return modified_files

def launch_app(directory):
    app = dash.Dash(__name__)
    
    # Inicialmente escanear el directorio
    includes = scan_directory(directory)
    elements, redundant_includes = build_elements(includes)
    
    app.layout = html.Div([
        html.H1("Visualizador de Dependencias C/C++", style={'textAlign': 'center'}),
        html.Div([
            html.Button("Layout jerárquico (vertical)", id='btn-hier', n_clicks=0),
            html.Button("Layout plano (horizontal)", id='btn-flat', n_clicks=0),
            html.Button("Layout libre (mover nodos)", id='btn-free', n_clicks=0),
            html.Button("Recargar directorio", id='btn-reload', n_clicks=0, 
                       style={'marginLeft': '20px', 'backgroundColor': '#4CAF50', 'color': 'white'}),
            html.Button("Eliminar includes redundantes", id='btn-remove-redundant', n_clicks=0,
                       style={'marginLeft': '20px', 'backgroundColor': '#FF5733', 'color': 'white'}),
        ], style={'textAlign': 'center', 'marginBottom': '10px'}),
        html.Div(id='status-message', style={'textAlign': 'center', 'marginBottom': '10px', 'color': 'green'}),
        html.Div(id='tooltip-output', style={'textAlign': 'center', 'marginBottom': '10px'}),
        # Contador de redundancias
        html.Div([
            html.Span("Includes redundantes detectados: "),
            html.Span(id='redundancy-count', children=str(len(redundant_includes))),
        ], style={'textAlign': 'center', 'marginBottom': '10px', 'fontWeight': 'bold'}),
        # Almacenar datos de redundancias en un componente oculto
        dcc.Store(id='redundant-data', data=redundant_includes),
        cyto.Cytoscape(
            id='cytoscape-graph',
            elements=elements,
            style={'width': '100%', 'height': '800px'},
            layout={'name': 'cose'},
            stylesheet=[
                {'selector': 'node', 'style': {
                    'label': 'data(label)',
                    'text-valign': 'center',
                    'color': 'white',
                    'background-color': '#0074D9',
                    'width': 'label',
                    'height': 'label',
                    'padding': '10px',
                    'shape': 'roundrectangle',
                    'font-size': '12px'
                }},
                {'selector': 'edge', 'style': {
                    'line-color': '#ccc',
                    'target-arrow-color': '#ccc',
                    'target-arrow-shape': 'triangle',
                    'curve-style': 'bezier'
                }},
                {'selector': '.redundant', 'style': {
                    'line-color': 'red',
                    'target-arrow-color': 'red',
                    'line-style': 'dashed',
                    'width': 3
                }}
            ]
        )
    ])

    @app.callback(
        Output('cytoscape-graph', 'layout'),
        Input('btn-hier', 'n_clicks'),
        Input('btn-flat', 'n_clicks'),
        Input('btn-free', 'n_clicks'),
        prevent_initial_call=True
    )
    def change_layout(n_hier, n_flat, n_free):
        button_id = ctx.triggered_id if ctx.triggered_id else 'no-id'
        if button_id == 'btn-hier':
            return {'name': 'breadthfirst', 'directed': True, 'spacingFactor': 2}
        elif button_id == 'btn-flat':
            return {'name': 'breadthfirst', 'directed': True, 'spacingFactor': 2, 'orientation': 'horizontal'}
        elif button_id == 'btn-free':
            return {'name': 'preset'}  # el usuario mueve los nodos
        return {'name': 'cose'}
    
    @app.callback(
        Output('tooltip-output', 'children'),
        Input('cytoscape-graph', 'tapEdgeData')
    )
    def display_edge_info(edge_data):
        if not edge_data:
            return "Haga clic en una arista para ver detalles"
        
        if 'tooltip' in edge_data:
            return html.Div([
                html.Strong("Detección de redundancia: "),
                html.Span(edge_data['tooltip']),
                html.Span(" - Se recomienda eliminar esta inclusión.")
            ], style={'color': 'red'})
        
        return f"Inclusión: {edge_data['source']} → {edge_data['target']}"
    
    @app.callback(
        [Output('cytoscape-graph', 'elements'),
         Output('status-message', 'children'),
         Output('redundant-data', 'data'),
         Output('redundancy-count', 'children')],
        [Input('btn-reload', 'n_clicks'),
         Input('btn-remove-redundant', 'n_clicks')],
        [State('cytoscape-graph', 'layout'),
         State('redundant-data', 'data')],
        prevent_initial_call=True
    )
    def update_graph(n_reload, n_remove, current_layout, redundant_data):
        button_id = ctx.triggered_id if ctx.triggered_id else 'no-id'
        
        # Obtener timestamp para el mensaje de estado
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        if button_id == 'btn-reload':
            # Re-escanear el directorio para obtener cambios
            new_includes = scan_directory(directory)
            new_elements, new_redundant_includes = build_elements(new_includes)
            status_message = f"Directorio recargado exitosamente a las {timestamp}"
            return new_elements, status_message, new_redundant_includes, str(len(new_redundant_includes))
            
        elif button_id == 'btn-remove-redundant':
            if not redundant_data:
                return dash.no_update, "No hay includes redundantes para eliminar", [], "0"
                
            # Eliminar los includes redundantes
            modified_files = remove_redundant_includes(redundant_data)
            
            # Re-escanear el directorio para actualizar el gráfico
            new_includes = scan_directory(directory)
            new_elements, new_redundant_includes = build_elements(new_includes)
            
            # Crear mensaje de estado
            if modified_files:
                status_message = f"Se eliminaron {len(modified_files)} includes redundantes a las {timestamp}. Archivos modificados: {len(modified_files)}"
            else:
                status_message = f"No se pudieron eliminar includes redundantes. Verifique permisos de archivos."
                
            return new_elements, status_message, new_redundant_includes, str(len(new_redundant_includes))
        
        # Por defecto, no actualizar
        return dash.no_update, dash.no_update, dash.no_update, dash.no_update

    app.run(debug=True)

# Ejecutar como script:
if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Uso: python visualizador.py <directorio>")
    else:
        launch_app(sys.argv[1])
