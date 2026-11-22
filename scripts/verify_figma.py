"""Read the configured file's metadata without spending another full-file read.
Run in the PM worker with its existing read-only token mount. Prints no secrets.
Metadata success is explicitly NOT proof of a completed selected-node crawl.
"""
import json
from pathlib import Path
from oijwef098234.gujeuk_ai.figma import FILE_KEY, figma_get


def verify(token_file=Path('/run/figma/token'),get=figma_get):
    if token_file.is_symlink() or not token_file.is_file():
        return {'metadata_access':False,'reason':'credential_missing','selected_node_verified':False}
    try:
        data=get('/v1/files/'+FILE_KEY+'/meta',token_file.read_text().strip())
        if not isinstance(data.get('file'),dict):raise ValueError('unexpected_metadata')
        return {'metadata_access':True,'file_key':FILE_KEY,'selected_node_verified':False}
    except Exception as error:
        return {'metadata_access':False,'error':type(error).__name__,
                'status':getattr(error,'status',None),'retry_at':getattr(error,'retry_at',0),
                'selected_node_verified':False}


if __name__=='__main__':print(json.dumps(verify()))
