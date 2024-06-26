import codecs
from collections.abc             import Iterable
from itertools                   import chain

from linkml_runtime.linkml_model import SchemaDefinition
from linkml.validator            import validate_file

import logging
from os      import replace
from os.path import isfile
from pathlib import PurePath
import re
from typing  import Optional

from fisdat.data_model import ManifestDesc, ScopeDesc, TableDesc

def fst(g):
    '''
    Utility function. RDFLib gives generators all over the place and
    usually we just want a single value.
    '''
    for e in g:
        return e
    raise Exception("Generator is empty")

def validation_helper (data         : str
                     , schema       : str
                     , target_class : str) -> bool:
    '''
    `validate_file()' either returns an empty list or a collection of
    errors in a report (`linkml.validator.report.ValidationReport').
    
    Setting the `strict' flag means that it fails on the first error,
    so we only get one. I think this behaviour is better as it catches
    the first error and should make it easier to fix.

    Compared to the hideous Python Traceback, these errors are remarkably
    friendly and informative!
    '''
    logging.debug (f"Called `validate_wrapper (data = {data}, schema = {schema}, target_class = {target_class})'")
    prereq_check = isfile (data) and isfile (schema)

    if (prereq_check):
        try: 
            report  = validate_file (data, schema, target_class, strict = True)
            results = report.results

            if (not results):
                logging.info (f"Validation success: data file {data} against schema file {schema}, with target class {target_class}")
                return (True)
            else:
                single_result = results[0]
                severity = single_result.severity
                problem  = single_result.message
                instance = single_result.instance
            
                print ("Validation error: ")
                print (f"-> Severity: {severity}")
                print (f"-> Message: {problem}")
                print (f"-> Trace: {instance}")
        
                return (False)
        except ValueError as e:
            print (f"Invalid target class {target_class}")
    else:
        print (f"Data file {data} and schema file {schema} must exist!")
        return (prereq_check)

def extension_helper (target_path : PurePath) -> str:
    '''
    Get the extension without the leading dot,
    to feed into `get_loader', `get_dumper' &c.
    '''
    target = str (target_path)
    if (len (target) == 0):
        return (target)
    else:
        return (target_path.suffix [1 : len (target_path.suffix)])
    
def prefix_helper (schema_definition : SchemaDefinition
                 , curie             : str
                 , fallback_uri      : str) -> str:
    '''
    This function mainly serves to try and expand a given prefix.
    The pre-requisite is to check the CURIE is in the right format and split it.

    1. If the prefix code/URI mappings are None, use the fallback prefix as we can't expand the default prefix code anyway
    2. If the prefix code/URI mappings are not None, try looking up the prefix matched, and expand or use fallback prefix
    3. If the prefix matched does not exist, try looking up the default prefix  and expand or use fallback prefixzip
    '''
    curie_pattern = re.compile (f"^([A-z|0-9|_]+):([A-z|0-9|_]+)$")
    curie_match   = curie_pattern.match(curie)

    if (curie_match is None):
        return (fallback_uri + curie)
    else:
        target_prefix_code = curie_match.group(1)
        target_term        = curie_match.group(2)

        default_prefix_code = schema_definition.default_prefix
        prefix_URI_mappings = schema_definition.prefixes

        if (prefix_URI_mappings is None):
            return (fallback_uri + target_term)
        else:
            target_prefix_mapped = prefix_URI_mappings.get (target_prefix_code)
            
            if (target_prefix_mapped is None):
                default_prefix_mapped = prefix_URI_mappings.get (default_prefix_code)

                if (default_prefix_mapped is None):
                    return (fallback_uri + target_term)
                else:
                    return (default_prefix_mapped.prefix_reference + target_term)
            else:
                return (target_prefix_mapped.prefix_reference + target_term)
    

def schema_components_helper (schema_obj) -> dict [str, str]:
    '''
    A shim which serialises the schema proper, to extract components of
    interest, so that they can be serialised in the manifest `tables'
    section.

    As regards to the slots, there are two elements to this.

    First, there are an arbitary number of slots which the schema may
    include. It is possible that some of these are actually top-level
    imports from a different schema.

    Second, there are the slots which are actually used to validate the
    data file, which are properties of the `TableSchema' implementation.

    However, it is this first superset of slots associated with
    `TableSchema' which include any notion of mappings.
    '''
    #logging.debug (f"Calling `schema_components_helper (schema = {schema})'")
    all_slots      = schema_obj.slots.items()
    target_columns = schema_obj.classes ["TableSchema"].slots
    #target_pairs   = {k:(v.exact_mappings, for (k,v) in all_slots if k in target_columns}

    get_mappings = lambda k, v : {
        "name":  k
      , "uri":   v.definition_uri
      , "super": v.is_a
      , "impl":  v.implements
      , "exact": v.exact_mappings
    }
    target_pairs = {k:get_mappings(k,v) for (k,v) in all_slots if k in target_columns}

    properties = {
        "title":       schema_obj.title
      , "atomic_name": schema_obj.name
      , "remote_path": schema_obj.id
      , "description": str (schema_obj.description or "") # empty string meaningful
      , "license":     schema_obj.license
      , "keywords":    schema_obj.keywords
      , "columns":     target_pairs
    }
    logging.debug (f"Extracted schema properties: {properties}")
    return (properties)

def take (iter : Iterable, n : int, ini : int = 0) -> Iterable:
    '''
    Get the first 'n' characters in an iterable.
    Note, pydantic actually has a type for positive numbers, &c.
    '''
    return (iter [ini:n])
    
def job_table (dataclass
              , manifest  : str   = "manifest.rdf"
              , preamble  : bool  = False
              , mode      : str   = 'w'
              , col_names : tuple[str,  ...] = ("data URI"
                                              , "data schema"
                                              , "data hash")) -> str:
    '''
    Tiny function to pretty-print tables. No need to pull in Pandas just
    to show a really simple JSON object in a table!
    '''
    tables       = dataclass.tables
    tuples       = [(k.resource_path, k.schema_path_yaml, k.resource_hash) for k in tables]
    tuples_extra = tuples + [col_names] # Potentially adjust column lengths
    
    file_len = max ([len (p[0]) for p in tuples_extra])
    spec_len = max ([len (p[1]) for p in tuples_extra])
    hash_len = len (col_names [2])
    row_len  = 2 + file_len + 3 + spec_len + 3 + hash_len + 2

    pad_item = lambda k, rl : k + (rl - len(k)) * ' '
    gen_row  = lambda p0, p1, p2, l0, l1, l2 : "".join (["| ", pad_item (p0, l0)
                                                      , " | ", pad_item (p1, l1)
                                                      , " | ", pad_item (p2, l2)
                                                      , " |"])
    border_row = '-' * row_len
    row_title  = gen_row (col_names[0], col_names[1], col_names[2], file_len, spec_len, hash_len)
    rows_body  = [gen_row (k[0], k[1], take(k[2],hash_len), file_len, spec_len, hash_len) for k in tuples]
    table_body = [border_row, row_title, border_row] + rows_body + [border_row]
 
    if (preamble):
        if (mode == 'w'):
            table_lead = f"Wrote to {manifest}:"
        elif (mode == 'r'):
            table_lead = f"Read from {manifest}:"
        else:
            table_lead = f"{manifest}:"
        table_text = '\n'.join ([table_lead] + table_body)
    else:
        table_text = '\n'.join (table_body)
    return (table_text)

class error(object):
    _strict = False
    @classmethod
    def strict(cls, strict):
        cls._strict = strict
    def __init__(self, s):
        """
        Raise an exception if we are in strict mode, otherwise just print
        things like validation errors.
        """
        if self._strict:
            raise Exception(s)
        else:
            print(s)
