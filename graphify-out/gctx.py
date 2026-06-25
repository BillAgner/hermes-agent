import json
import networkx as nx
from networkx.readwrite import json_graph
from pathlib import Path

data = json.loads(Path("graphify-out/graph.json").read_text(encoding="utf-8"))
G = json_graph.node_link_graph(data, edges="links")

agent_node = "mcp_servers_mcp_agent_src_mcp_agent_agents_agent_py_agents_agent_agent"
agg_node   = "mcp_agent_mcp_mcp_aggregator"

# Show Agent neighbors, focusing on MCP and LLM sides
print(f"=== Agent node neighbors (degree={G.degree(agent_node)}) ===")
neighbors = list(G.neighbors(agent_node))
for nb in neighbors[:30]:
    label = G.nodes[nb].get("label", nb)
    sf = G.nodes[nb].get("source_file", "")
    raw = G[agent_node][nb]
    edge = next(iter(raw.values()), {}) if isinstance(G, nx.MultiGraph) else raw
    rel = edge.get("relation","")
    conf = edge.get("confidence","")
    print(f"  --{rel}[{conf}]--> {label}  ({sf})")

print()
print(f"=== MCPAggregator node neighbors (degree={G.degree(agg_node)}) ===")
for nb in list(G.neighbors(agg_node))[:20]:
    label = G.nodes[nb].get("label", nb)
    sf = G.nodes[nb].get("source_file","")
    raw = G[agg_node][nb]
    edge = next(iter(raw.values()), {}) if isinstance(G, nx.MultiGraph) else raw
    print(f"  --{edge.get('relation','')}[{edge.get('confidence','')}]--> {label}  ({sf})")
