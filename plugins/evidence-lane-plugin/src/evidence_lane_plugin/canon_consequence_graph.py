"""Read-only task exchange authority consequence projections over project-owned records.

Preserves participant, contract, exchange, reply/backfire/correction and evidence
relationships. Graphs neither admit task exchange authority input nor acquire another authority.
"""
from __future__ import annotations

from .canon_task_graph import CanonRead, CanonStore
from .lane_contract import ViewGraph, content_digest


def canon_view(store, scope):
    page = CanonStore(store).read(CanonRead(limit=min(scope.node_limit, 50), include_payload=True))
    graph = ViewGraph(store.project_id, scope)
    participants, contracts, exchanges = {}, {}, {}
    for item in page.participants:
        participants[(store.project_id,item["participant_id"])] = graph.node("canon_participant", item["participant_id"], item["label"],
            locator={"owner_client_id": item["owner_client_id"], "owner_generation": item.get("owner_generation", 1),
                     "project_id":store.project_id,"participant_id":item["participant_id"],"native_task_attestation": "not_provided"})
    for item in page.contracts:
        contract = item["contract"]
        contracts[item["contract_digest"]] = graph.node("canon_contract", item["contract_digest"], contract["contract_key"],
            state="active" if contract["active"] else "inactive",
            locator={"receiver_id": contract["receiver_id"], "version": item["version"]})
        graph.edge(participants.get((store.project_id,contract["receiver_id"])), contracts[item["contract_digest"]], "EXPECTS")
    def endpoint(project_id,participant_id):
        key = project_id,participant_id
        if key not in participants:
            participants[key] = graph.node('canon_participant',project_id+':'+participant_id,
                'Referenced participant '+participant_id,locator={'project_id':project_id,'participant_id':participant_id,
                    'ownership_evidence':'endpoint_in_local_envelope','native_task_attestation':'not_provided'})
        return participants[key]

    def exchange_reference(project_id,identity,digest=None):
        if project_id == store.project_id and identity in exchanges:
            return exchanges[identity]
        return graph.node('canon_exchange_reference',project_id+':'+identity,'Referenced exchange '+identity,
            locator={'record_project_id':project_id,'exchange_id':identity,'envelope_digest':digest,
                'evidence':'locator_in_local_envelope','receiver_state_observed':False})

    for item in page.exchanges:
        for project_id,participant_id in ((item['source_project_id'],item['sender_id']),(item['destination_project_id'],item['receiver_id'])):
            endpoint(project_id,participant_id)
        exchanges[item["exchange_id"]] = graph.node("canon_exchange", item["exchange_id"], item["kind"] + ": " + item["summary"],
            state=item["state"], locator={"envelope_digest": item["envelope_digest"], "version": item["version"],
                "source_client_id": item["source_client_id"], "native_task_attestation": "not_provided",
                "source_project_id":item['source_project_id'], "destination_project_id":item['destination_project_id'],
                "local_role":item['local_role']})
    for item in page.exchanges:
        node = exchanges[item["exchange_id"]]
        message = item["envelope"]["message"]
        graph.edge(participants.get((item['source_project_id'],item["sender_id"])), node, "SENT")
        graph.edge(node, participants.get((item['destination_project_id'],item["receiver_id"])),
            'ADDRESSES' if item['local_role']=='outbox' else "RECEIVER_" + item["state"].upper())
        if message['reply_to']:
            graph.edge(node,exchange_reference(item['source_project_id'],message['reply_to']), 'REPLIES_TO')
        if message.get('backfire'):
            backfire = message['backfire']
            admitted = exchange_reference(item['source_project_id'],backfire['admitted_exchange_id'],backfire['admitted_envelope_digest'])
            graph.edge(node,admitted,'REQUESTS_REVISION',provenance='source_owned_admitted_input',
                evidence={key:backfire[key] for key in ('failure_class','requested_revision','automatic_retry_allowed')})
            route = backfire['return_route']
            graph.edge(node,endpoint(route['project_id'],route['participant_id']),'DECLARES_RETURN_ROUTE')
            for sequence,participant in enumerate(backfire['trace'],1):
                graph.edge(node,endpoint(participant['project_id'],participant['participant_id']),
                    'CORRECTION_TRACE',provenance='immutable_backfire_route',evidence={'exchange_id':item['exchange_id'],'sequence':sequence})
        if message["supersedes"]:
            graph.edge(node, exchanges.get(message["supersedes"]), "CORRECTS")
        if item.get('supersession'):
            link = item['supersession']
            graph.edge(exchanges.get(link['successor_id']), node, 'SUPERSEDES',
                provenance=link['decision_basis'], evidence={'envelope_digest':link['successor_envelope_digest']})
        if message["expected_contract"]:
            graph.edge(node, contracts.get(message["expected_contract"]), "ADDRESSES_CONTRACT")
        for reference in message["payload"]["references"]:
            locator = graph.node("evidence_reference", content_digest({**reference,"source_project_id":item["source_project_id"]}),
                reference["kind"] + ": " + reference["key"], locator={**reference,"source_project_id":item["source_project_id"]})
            graph.edge(node, locator, "CITES")
    graph.truncated |= page.truncated or page.participants_truncated or page.contracts_truncated
    return graph.result()


def canon_pointer(binding, graph, files):
    return {"schema": "evidence-lane.canon-consequence-navigation.v4",
        "project_id": binding["project_id"], "snapshot_binding": binding,
        "participants": {node["key"]: node["id"] for node in graph["nodes"] if node["kind"] == "canon_participant"},
        "exchanges": {node["key"]: {"node_id": node["id"], "local_state":node['state'],
                      "local_role":node['locator']['local_role'],
                      "receiver_state":None if node['locator']['local_role']=='outbox' else node["state"]}
                      for node in graph["nodes"] if node["kind"] == "canon_exchange"},
        "artifacts": files, "acceptance_authority": "canon_sqlite", "native_task_attestation": "not_provided"}
