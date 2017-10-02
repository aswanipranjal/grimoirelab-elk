#!/usr/bin/python3
# -*- coding: utf-8 -*-
#
# Copy JSON items from MongoDB to Elasticsearch
# If the collection is a OSSMeter one add project and other fields to items
#
# Copyright (C) 2017 Bitergia
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.
#
# Authors:
#   Alvaro del Castillo San Felix <acs@bitergia.com>
#

import argparse
import logging

from pprint import pprint

from pymongo import MongoClient

from grimoire_elk.elk.elastic import ElasticSearch

def get_params():
    parser = argparse.ArgumentParser(usage="usage: mongo2es [options]",
                                     description="Import mongo items in ElasticSearch")
    parser.add_argument("-a", "--all-collections", action='store_true', help="Process all MongoDB Collections")
    parser.add_argument("-c", "--collection", help="MongoDB Collection")
    parser.add_argument("-m", "--mongo-host", default='localhost', help="MongoDB Host")
    parser.add_argument("-p", "--mongo-port", default='27017', type=int, help="MongoDB Port")
    parser.add_argument("-e", "--elastic-url", required=True, help="ElasticSearch URL")
    parser.add_argument("-i", "--index", required=True, help="ElasticSearch index in which to import the mongodb items")
    parser.add_argument('-g', '--debug', dest='debug', action='store_true')
    args = parser.parse_args()

    if not args.collection and not args.all_collections:
        parser.error("--collection or --all-collections needed")

    return args

def connect_to_mongo(host=None, port=None):
    """ Return a connection to the mongo server in host and port """
    if host and port:
        client = MongoClient(host, port)
    elif host:
        client = MongoClient(host)
    else:
        client = MongoClient()

    return client

def is_ossmeter_historic_collection(collection):
    # Check if a collection is an OSSMeter one
    # Sample: modeling-graphiti.historic.newsgroups.articles

    is_ossmeter = False

    if len(collection.split(".")) == 4:
        is_ossmeter = True

    return is_ossmeter

def fetch_mongodb_all(host=None, port=None):
    logging.info("Searching for all OSSMeter metrics collections")
    client = connect_to_mongo(host, port)

    # Find all OSSMeter collections in mongo
    for db in client.database_names():
        for collection in client[db].collection_names():
            collection_name = db + '.' + collection
            if is_ossmeter_historic_collection(collection_name):
                logging.debug('Loading items from %s', collection_name)
                for item in fetch_mongodb_collection(collection_name, client=client):
                    yield item

def extract_metrics(item, item_meta):
    # Extract metric names and values from an item

    def create_item_metric(field, value):
        item_metric = {}
        item_metric['metric_es_name'] = field
        item_metric['metric_es_value'] = value
        item_metric['metric_es_cumulative'] = 0
        item_metric['metric_es_average'] = 0
        if 'cumulative' in field:
            item_metric['metric_es_cumulative'] = 1
        if 'avg' in field:
            item_metric['metric_es_average'] = 1

        return item_metric



    item_metrics = []

    no_value_fields = ['__date', '_type', '_id', '__datetime', 'bugs',
                       'bugData', 'bugTrackers', 'newsgroups',
                       'bugTrackerId', 'percentage']
    value_fields = list(set(item.keys()) - set(no_value_fields))

    for field in value_fields:
        value = item[field]
        if not isinstance(item[field], (int, float)):
            value = None
            if isinstance(item[field], list):
                for subitem in item[field]:
                    subitem_metric_name = None
                    subitem_metric_value = None
                    subvalue_fields = list(set(subitem.keys()) - set(no_value_fields))
                    # numberOfBugs 5
                    # severityLevel enhancement
                    for subfield in subvalue_fields:
                        # Just support number metrics
                        if not isinstance(subitem[subfield], (int, float)):
                            # This must be the name of the subitem_metric_value
                            subitem_metric_name = field + "_" + subitem[subfield]
                        else:
                            subitem_metric_value = subitem[subfield]
                    # print(subitem_metric_name, subitem_metric_value)
                    item_metric = create_item_metric(subitem_metric_name, subitem_metric_value)
                    item_metrics.append(item_metric)

        item_metric = create_item_metric(field, value)

        item_metrics.append(item_metric)

    # logging.info("Metrics found: %s", item_metrics)

    return item_metrics


def enrich_ossmeter_item(item, item_meta):
    # Given a ossmeter item enrich it to be used in Kibana

    # A raw item from OSSMeter could generate several enriched items, one for
    # each metric
    eitems = []

    for metrics in extract_metrics(item, item_meta):
        eitem = {}
        eitem.update(metrics)
        # It is useful to have all item fields for debugging
        eitem.update(item)

        if '__datetime' in item:
            eitem['datetime'] = eitem['__datetime'].isoformat()
            eitem['__datetime'] = eitem['__datetime'].isoformat()
        if '__date' in item:
            eitem['date'] = item['__date']
        eitem['mongo_id'] = eitem.pop('_id')
        eitem['mongo_type'] = eitem.pop('_type')
        eitem['id'] = eitem['mongo_id'] + "_" + eitem['metric_es_name']

        eitems.append(eitem)

    return eitems


def fetch_mongodb_collection(collection_str, host=None, port=None, client=None):
    """ conn could be a already created connection to Mongo """
    if not client:
        client = connect_to_mongo(host, port)

    collection = None
    item_meta = {
        'project': None,
        'metric_type': None,
        'metric_class': None,
        'metric_name': None
    }
    if "." in collection_str:
        # Sample: modeling-graphiti.historic.newsgroups.articles
        subcollections = collection_str.split(".")
        if len(subcollections) != 4:
            logging.warning('%s is not a OSSMeter collection', collection_str)
        else:
            item_meta = {
                'project': subcollections[0],
                'metric_type': subcollections[1],
                'metric_class': subcollections[2],
                'metric_name': subcollections[3]
            }

        for col in subcollections:
            client = client[col]
        collection = client
    else:
        collection = client[collection_str]

    for item in collection.find():
        enrich_items = enrich_ossmeter_item(item, item_meta)
        for eitem in enrich_items:
            eitem.update(item_meta)
            # print(eitem)
            yield eitem

if __name__ == '__main__':

    args = get_params()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(message)s')
        logging.debug("Debug mode activated")
    else:
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')

    logging.info("Importing items from %s to %s/%s", args.collection, args.elastic_url, args.index)

    elastic = ElasticSearch(args.elastic_url, args.index)

    if args.collection:
        mongo_items = fetch_mongodb_collection(args.collection, args.mongo_host, args.mongo_port)
    elif args.all_collections:
        mongo_items = fetch_mongodb_all(args.mongo_host, args.mongo_port)
    else:
        raise RuntimeError('Collection to be processed not provided')

    if mongo_items:
        logging.info("Loading collections in Elasticsearch")
        elastic.bulk_upload_sync(mongo_items, "id")
