import socket
import threading
import json
import struct
import time
import os
import logging
from datetime import datetime

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class RelayServer:
    def __init__(self, host='0.0.0.0', port=None):
        self.host = host
        # Railway automatically sets PORT environment variable
        self.port = port or int(os.environ.get('PORT', 8888))
        self.hosts = {}  # ID -> socket mapping
        self.clients = {}  # socket -> target_id mapping
        self.connections = {}  # Track all connections
        self.stats = {
            'total_connections': 0,
            'active_connections': 0,
            'data_transferred': 0,
            'start_time': datetime.now()
        }
        
    def start(self):
        """Start the relay server"""
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.host, self.port))
            server.listen(100)
            
            logger.info("=" * 60)
            logger.info("🌐 PyDesk Relay Server Started!")
            logger.info("=" * 60)
            logger.info(f"📡 Host: {self.host}")
            logger.info(f"🔌 Port: {self.port}")
            logger.info(f"⏰ Started at: {self.stats['start_time'].strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info("=" * 60)
            logger.info("✅ Server is ready to accept connections...")
            logger.info("=" * 60)
            
            # Start stats thread
            threading.Thread(target=self.log_stats, daemon=True).start()
            
            while True:
                try:
                    client, addr = server.accept()
                    self.stats['total_connections'] += 1
                    self.stats['active_connections'] += 1
                    
                    logger.info(f"✓ New connection from {addr[0]}:{addr[1]}")
                    logger.info(f"📊 Active connections: {self.stats['active_connections']}")
                    
                    # Handle client in new thread
                    threading.Thread(
                        target=self.handle_client, 
                        args=(client, addr), 
                        daemon=True
                    ).start()
                    
                except KeyboardInterrupt:
                    logger.info("\n🛑 Server shutting down...")
                    break
                except Exception as e:
                    logger.error(f"✗ Accept error: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"✗ Server startup failed: {e}")
            raise
    
    def handle_client(self, client, addr):
        """Handle individual client connection"""
        connection_start = time.time()
        bytes_transferred = 0
        
        try:
            # Set timeout for initial message
            client.settimeout(30)
            
            # Receive initial message
            msg = self.receive_json(client)
            
            if not msg:
                logger.warning(f"⚠️  No initial message from {addr}")
                return
            
            # Remove timeout after handshake
            client.settimeout(None)
            
            if msg.get('action') == 'register':
                # Host registering
                host_id = msg.get('id')
                if not host_id:
                    logger.error(f"✗ No ID provided by host {addr}")
                    return
                
                self.hosts[host_id] = client
                self.connections[client] = {
                    'type': 'host',
                    'id': host_id,
                    'addr': addr,
                    'connected_at': datetime.now()
                }
                
                logger.info(f"📡 Host registered: {host_id} from {addr[0]}")
                logger.info(f"📋 Total hosts: {len(self.hosts)}")
                
                # Keep connection alive and forward data
                try:
                    while True:
                        # Check if there's a connected client
                        client_socket = None
                        for sock, target_id in self.clients.items():
                            if target_id == host_id:
                                client_socket = sock
                                break
                        
                        if client_socket:
                            # Receive data from host
                            data = client.recv(8192)
                            if not data:
                                break
                            
                            bytes_transferred += len(data)
                            self.stats['data_transferred'] += len(data)
                            
                            # Forward to client
                            try:
                                client_socket.sendall(data)
                            except:
                                logger.warning(f"⚠️  Failed to forward data to client")
                                break
                        else:
                            # No client connected, just wait
                            time.sleep(0.1)
                            
                except Exception as e:
                    logger.error(f"✗ Host {host_id} error: {e}")
                
            elif msg.get('action') == 'connect':
                # Client connecting to host
                target_id = msg.get('target_id')
                
                if not target_id:
                    logger.error(f"✗ No target ID from {addr}")
                    response = {'action': 'error', 'error': 'No target ID provided'}
                    self.send_json(client, response)
                    return
                
                logger.info(f"🔗 Client requesting connection to host: {target_id}")
                
                if target_id in self.hosts:
                    host_socket = self.hosts[target_id]
                    self.clients[client] = target_id
                    self.connections[client] = {
                        'type': 'client',
                        'target_id': target_id,
                        'addr': addr,
                        'connected_at': datetime.now()
                    }
                    
                    # Notify host
                    try:
                        response = {'action': 'client_connected'}
                        self.send_json(host_socket, response)
                        logger.info(f"✓ Notified host {target_id}")
                    except Exception as e:
                        logger.error(f"✗ Failed to notify host: {e}")
                        response = {'action': 'error', 'error': 'Host connection failed'}
                        self.send_json(client, response)
                        return
                    
                    # Notify client
                    response = {'action': 'connected'}
                    self.send_json(client, response)
                    
                    logger.info(f"✓ Connected client {addr[0]} to host {target_id}")
                    logger.info(f"📊 Active sessions: {len(self.clients)}")
                    
                    # Forward all data from client to host
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
                                logger.warning(f"⚠️  Failed to forward to host {target_id}")
                                break
                                
                    except Exception as e:
                        logger.error(f"✗ Client forwarding error: {e}")
                    
                else:
                    logger.warning(f"⚠️  Host not found: {target_id}")
                    logger.info(f"📋 Available hosts: {list(self.hosts.keys())}")
                    response = {
                        'action': 'error', 
                        'error': f'Host {target_id} not found. Host must start hosting first.'
                    }
                    self.send_json(client, response)
                    
        except socket.timeout:
            logger.warning(f"⚠️  Connection timeout from {addr}")
        except Exception as e:
            logger.error(f"✗ Error with {addr}: {e}")
        finally:
            # Cleanup
            connection_duration = time.time() - connection_start
            
            # Remove from hosts
            for host_id, sock in list(self.hosts.items()):
                if sock == client:
                    del self.hosts[host_id]
                    logger.info(f"📡 Host {host_id} disconnected")
                    logger.info(f"⏱️  Duration: {connection_duration:.1f}s | Data: {bytes_transferred/1024:.1f}KB")
            
            # Remove from clients
            if client in self.clients:
                target_id = self.clients[client]
                del self.clients[client]
                logger.info(f"🔗 Client disconnected from host {target_id}")
                logger.info(f"⏱️  Duration: {connection_duration:.1f}s | Data: {bytes_transferred/1024:.1f}KB")
            
            # Remove from connections tracking
            if client in self.connections:
                del self.connections[client]
            
            self.stats['active_connections'] -= 1
            logger.info(f"📊 Active connections: {self.stats['active_connections']}")
            
            try:
                client.close()
            except:
                pass
    
    def send_json(self, sock, data):
        """Send JSON data with size header"""
        try:
            json_data = json.dumps(data).encode('utf-8')
            size = struct.pack('!I', len(json_data))
            sock.sendall(size + json_data)
            return True
        except Exception as e:
            logger.error(f"✗ Send error: {e}")
            return False
    
    def receive_json(self, sock):
        """Receive JSON data with size header"""
        try:
            # Receive size header
            size_data = self.receive_all(sock, 4)
            if not size_data:
                return None
            
            size = struct.unpack('!I', size_data)[0]
            
            # Sanity check
            if size > 10 * 1024 * 1024:  # 10MB max
                logger.error(f"✗ Message too large: {size} bytes")
                return None
            
            # Receive JSON data
            json_data = self.receive_all(sock, size)
            if not json_data:
                return None
            
            return json.loads(json_data.decode('utf-8'))
            
        except Exception as e:
            logger.error(f"✗ Receive error: {e}")
            return None
    
    def receive_all(self, sock, size):
        """Helper to receive exact amount of data"""
        data = b''
        while len(data) < size:
            try:
                packet = sock.recv(min(size - len(data), 4096))
                if not packet:
                    return None
                data += packet
            except Exception as e:
                logger.error(f"✗ Receive chunk error: {e}")
                return None
        return data
    
    def log_stats(self):
        """Periodically log server statistics"""
        while True:
            time.sleep(300)  # Every 5 minutes
            uptime = datetime.now() - self.stats['start_time']
            
            logger.info("=" * 60)
            logger.info("📊 SERVER STATISTICS")
            logger.info("=" * 60)
            logger.info(f"⏰ Uptime: {uptime}")
            logger.info(f"🔌 Total connections: {self.stats['total_connections']}")
            logger.info(f"✅ Active connections: {self.stats['active_connections']}")
            logger.info(f"📡 Active hosts: {len(self.hosts)}")
            logger.info(f"🔗 Active clients: {len(self.clients)}")
            logger.info(f"💾 Data transferred: {self.stats['data_transferred']/1024/1024:.2f} MB")
            logger.info("=" * 60)

def main():
    """Main entry point"""
    try:
        server = RelayServer()
        server.start()
    except KeyboardInterrupt:
        logger.info("\n👋 Server stopped by user")
    except Exception as e:
        logger.error(f"💥 Fatal error: {e}")
        raise

if __name__ == "__main__":
    main()