import os
import psycopg2
from psycopg2 import Error
from . import models

db_host = os.getenv('FDA_DATABASE_HOST')
db_name = os.getenv('FDA_DATABASE_NAME')
db_user = os.getenv('FDA_DATABASE_USER')
db_password = os.getenv('FDA_DATABASE_PASSWORD')

def create_document(user_id, document_id, filename, status):
    try:
        connection = psycopg2.connect(
            dbname=db_name,
            user=db_user,
            password=db_password,
            host=db_host
        )
        cursor = connection.cursor()
        cursor.execute('INSERT INTO fda_documents (user_id, document_id, filename, status) VALUES (%s, %s, %s, %s)', (user_id, document_id, filename, status))
        connection.commit()
    except (Exception, Error) as error:
        print('Error while creating document:', error)
