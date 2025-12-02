#!/usr/bin/env python3
import socket
import threading
import json
import struct
import time
import os
import logging
import sys
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

class HealthCheckHandler(BaseHTTPRequestHandler):
    """Handle HTTP health checks"""
    def do_GET(self):
        if self.path == '/health' or self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            status = {
                'status': 'ok',
                'uptime': str(datetime.now() - server_instance.stats['start_time']),
                'connections': server_instance.stats['active_connections'],
                'hosts': len(server_instance.hosts),
                'clients': len(server_instance.clients)
            }
            self.wfile.write(json.dumps(status).encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass  # Suppress HTTP logs

class RelayServer:
    def __init__(self, host='0.0.0.0', port=None):
        self.host = host
        # Railway sets PORT automatically
        self.port = port or int(os.environ.get('PORT', 8888))
        self.hosts = {}
        self.clients = {}
        self.connections = {}
        self.stats = {
            'total_connections': 0,
            'active_connections': 0,
            'data_transferred': 0,
            'start_time': datetime.now()
        }
        global server_instance
        server_instance = self
        
    def start(self):
        """Start relay server with HTTP health check"""
        try:
            # Start HTTP health check server on same port
            logger.info("=" * 60)
            logger.info("🌐 PyDesk Relay Server - Railway Edition")
            logger.info("=" * 60)
            logger.info(f"📡 Host: {self.host}")
            logger.info(f"🔌 Port: {self.port}")
            logger.info(f"⏰ Started: {self.stats['start_time'].strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info("=" * 60)
            
            # Start TCP server in thread
            tcp_thread = threading.Thread(target=self.run_tcp_server, daemon=True)
            tcp_thread.start()
            
            # Start HTTP health check (Railway needs this!)
            logger.info("🏥 Starting HTTP health check server...")
            http_server = HTTPServer((self.host, self.port), HealthCheckHandler)
            
            logger.info("✅ Server is READY!")
            logger.info(f"🌐 Health check: http://0.0.0.0:{self.port}/health")
            logger.info("=" * 60)
            
            # This keeps Railway happy
            http_server.serve_forever()
            
        except Exception as e:
            logger.error(f"💥 Server startup failed: {e}")
            raise
    
    def run_tcp_server(self):
        """Run TCP relay server"""
        try:
            # Use different port for TCP (Railway might block same port)
            tcp_port = self.port + 1
            
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.host, tcp_port))
            server.listen(100)
            
            logger.info(f"📡 TCP Relay listening on port {tcp_port}")
            
            # Stats logger
            threading.Thread(target=self.log_stats, daemon=True).start()
            
            while True:
                try:
                    client, addr = server.accept()
                    self.stats['total_connections'] += 1
                    self.stats['active_connections'] += 1
                    
                    logger.info(f"✓ Connection from {addr[0]}:{addr[1]}")
                    
                    threading.Thread(
                        target=self.handle_client,
                        args=(client, addr),
                        daemon=True
                    ).start()
                    
                except Exception as e:
                    logger.error(f"✗ Accept error: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"✗ TCP server error: {e}")
    
    def handle_client(self, client, addr):
        """Handle client connection"""
        connection_start = time.time()
        bytes_transferred = 0
        
        try:
            client.settimeout(30)
            msg = self.receive_json(client)
            
            if not msg:
                logger.warning(f"⚠️  No message from {addr}")
                return
            
            client.settimeout(None)
            
            if msg.get('action') == 'register':
                host_id = msg.get('id')
                if not host_id:
                    return
                
                self.hosts[host_id] = client
                logger.info(f"📡 Host registered: {host_id}")
                
                try:
                    while True:
                        client_socket = None
                        for sock, target_id in self.clients.items():
                            if target_id == host_id:
                                client_socket = sock
                                break
                        
                        if client_socket:
                            data = client.recv(8192)
                            if not data:
                                break
                            
                            bytes_transferred += len(data)
                            self.stats['data_transferred'] += len(data)
                            
                            try:
                                client_socket.sendall(data)
                            except:
                                break
                        else:
                            time.sleep(0.1)
                            
                except Exception as e:
                    logger.error(f"✗ Host error: {e}")
                
            elif msg.get('action') == 'connect':
                target_id = msg.get('target_id')
                
                if not target_id:
                    response = {'action': 'error', 'error': 'No target ID'}
                    self.send_json(client, response)
                    return
                
                logger.info(f"🔗 Client → {target_id}")
                
                if target_id in self.hosts:
                    host_socket = self.hosts[target_id]
                    self.clients[client] = target_id
                    
                    try:
                        response = {'action': 'client_connected'}
                        self.send_json(host_socket, response)
                    except:
                        response = {'action': 'error', 'error': 'Host failed'}
                        self.send_json(client, response)
                        return
                    
                    response = {'action': 'connected'}
                    self.send_json(client, response)
                    
                    logger.info(f"✓ Connected to {target_id}")
                    
                    try:
                        while True:
                            data = client.recv(8192)
                            if not data:
                                break
                            
                            bytes_transferred += len(data)
                            self.stats['data_transferred'] += len(data)
                            
                            try:
                                host_socket.sendall(data)
                            except:
                                break
                    
                    except Exception as e:
                        logger.error(f"✗ Forward error: {e}")
                
                else:
                    logger.warning(f"⚠️  Host not found: {target_id}")
                    response = {
                        'action': 'error',
                        'error': f'Host {target_id} not found'
                    }
                    self.send_json(client, response)
        
        except socket.timeout:
            logger.warning(f"⚠️  Timeout: {addr}")
        except Exception as e:
            logger.error(f"✗ Error: {e}")
        finally:
            duration = time.time() - connection_start
            
            for host_id, sock in list(self.hosts.items()):
                if sock == client:
                    del self.hosts[host_id]
                    logger.info(f"📡 Host {host_id} left ({duration:.1f}s, {bytes_transferred/1024:.1f}KB)")
            
            if client in self.clients:
                target_id = self.clients[client]
                del self.clients[client]
                logger.info(f"🔗 Client left {target_id} ({duration:.1f}s, {bytes_transferred/1024:.1f}KB)")
            
            if client in self.connections:
                del self.connections[client]
            
            self.stats['active_connections'] -= 1
            
            try:
                client.close()
            except:
                pass
    
    def send_json(self, sock, data):
        try:
            json_data = json.dumps(data).encode('utf-8')
            size = struct.pack('!I', len(json_data))
            sock.sendall(size + json_data)
            return True
        except:
            return False
    
    def receive_json(self, sock):
        try:
            size_data = self.receive_all(sock, 4)
            if not size_data:
                return None
            
            size = struct.unpack('!I', size_data)[0]
            if size > 10 * 1024 * 1024:
                return None
            
            json_data = self.receive_all(sock, size)
            if not json_data:
                return None
            
            return json.loads(json_data.decode('utf-8'))
        except:
            return None
    
    def receive_all(self, sock, size):
        data = b''
        while len(data) < size:
            try:
                packet = sock.recv(min(size - len(data), 4096))
                if not packet:
                    return None
                data += packet
            except:
                return None
        return data
    
    def log_stats(self):
        while True:
            time.sleep(300)
            uptime = datetime.now() - self.stats['start_time']
            
            logger.info("=" * 60)
            logger.info("📊 STATS")
            logger.info(f"⏰ Uptime: {uptime}")
            logger.info(f"🔌 Total: {self.stats['total_connections']}")
            logger.info(f"✅ Active: {self.stats['active_connections']}")
            logger.info(f"📡 Hosts: {len(self.hosts)}")
            logger.info(f"🔗 Clients: {len(self.clients)}")
            logger.info(f"💾 Data: {self.stats['data_transferred']/1024/1024:.2f} MB")
            logger.info("=" * 60)

def main():
    try:
        server = RelayServer()
        server.start()
    except KeyboardInterrupt:
        logger.info("\n👋 Stopped")
    except Exception as e:
        logger.error(f"💥 Fatal: {e}")
        raise

if __name__ == "__main__":
    main()
