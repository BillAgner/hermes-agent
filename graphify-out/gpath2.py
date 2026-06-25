import json, sys
import networkx as nx
from networkx.readwrite import json_graph
from pathlib import Path

data = json.loads(Path("graphify-out/graph.json").read_text(encoding="utf-8"))
G = json_graph.node_link_graph(data, edges="links")

src = "mcp_agent_mcp_mcp_aggregator"
tgt = "mcp_servers_mcp_servers_mcp_agent_src_mcp_agent_workflows_llm_augmented_llm_py_llm_augmented_llm_augmentedllm"

print(f"Src: {G.nodes.get(src, {}).get('label','NOT FOUND')}")
print(f"Tgt: {G.nodes.get(tgt, {}).get('label','NOT FOUND')}")
print(f"Src exists: {src in G.nodes}")
print(f"Tgt exists: {tgt in G.nodes}")
print(f"Graph directed: {G.is_directed()}")

if src in G.nodes and tgt in G.nodes:
    try:
        path = nx.shortest_path(G, src, tgt)
        print(f"Directed path: {len(path)-1} hops")
        for i, nid in enumerate(path):
            label = G.nodes[nid].get("label", nid)
            sf = G.nodes[nid].get("source_file", "")
            if i < len(path) - 1:
                nxt = path[i+1]
                raw = G[nid][nxt]
                edge = next(iter(raw.values()), {}) if isinstance(G, nx.MultiGraph) else raw
                print(f"  [{i}] {label}  ({sf})")
                print(f"       --{edge.get('relation','')}-->  [{edge.get('confidence','')} {edge.get('confidence_score','')}]")
            else:
                print(f"  [{i}] {label}  ({sf})")
    except nx.NetworkXNoPath:
        print("No directed path - trying undirected")
        UG = G.to_undirected()
        path = nx.shortest_path(UG, src, tgt)
        print(f"Undirected path: {len(path)-1} hops")
        for nid in path:
            print(f"  {G.nodes[nid].get('label',nid)}  ({G.nodes[nid].get('source_file','')})")
