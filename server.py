
import socket
import io
import datetime
import os
import time

import google.generativeai as genai
from elevenlabs.client import ElevenLabs

# --- 🚨 IMPORTANT: API Key Configuration ---
# Replace with your actual API keys. Do not share them publicly.
GEMINI_API_KEY = "GEMINI_API_KEY"
ELEVENLABS_API_KEY = "ELEVENLABS_API_KEY"

# Configure APIs
genai.configure(api_key=GEMINI_API_KEY)
elevenlabs_client = ElevenLabs(api_key=ELEVENLABS_API_KEY)

# --- Model & Voice Configuration ---
GEMINI_MODEL_NAME = "gemini-1.5-flash"
# Find your desired voice ID on the ElevenLabs website.
ELEVENLABS_VOICE_ID = "EXAVITQu4vr4xnSDxMaL"  # Example: "Rachel"

# --- 🌐 Network and Audio Configuration ---
HOST = '0.0.0.0'
PORT = 50007
SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 2 bytes for 16-bit audio
CHUNK_SIZE = 1024
END_RECORDING_SIGNAL = b"END_RECORDING_SIGNAL"


# --- Helper function to get local IP ---
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# --- Helper to create a WAV header for raw PCM data ---
def create_wav_header(audio_data, sample_rate, num_channels, sample_width):
    datasize = len(audio_data)
    header = io.BytesIO()
    header.write(b'RIFF')
    header.write((datasize + 36).to_bytes(4, 'little'))
    header.write(b'WAVEfmt ')
    header.write((16).to_bytes(4, 'little'))
    header.write((1).to_bytes(2, 'little'))
    header.write(num_channels.to_bytes(2, 'little'))
    header.write(sample_rate.to_bytes(4, 'little'))
    header.write((sample_rate * num_channels * sample_width).to_bytes(4, 'little'))
    header.write((num_channels * sample_width).to_bytes(2, 'little'))
    header.write((sample_width * 8).to_bytes(2, 'little'))
    header.write(b'data')
    header.write(datasize.to_bytes(4, 'little'))
    return header.getvalue() + audio_data


# --- Function to generate and stream audio back to ESP32 ---
def generate_and_stream_elevenlabs_audio(text, conn):
    if not text.strip():
        print("[ElevenLabs] No text to synthesize.")
        return

    print(f"[ElevenLabs] Generating and streaming audio for: '{text}'")
    try:
        # Request audio as a raw 16kHz PCM stream for direct playback on ESP32
        audio_stream = elevenlabs_client.text_to_speech.convert(
            voice_id=ELEVENLABS_VOICE_ID,
            model_id="eleven_multilingual_v2",
            text=text,
            output_format="pcm_16000"  # IMPORTANT: Raw audio for ESP32
        )

        # Stream the audio chunks directly to the ESP32
        total_bytes_sent = 0
        for chunk in audio_stream:
            if chunk:
                conn.sendall(chunk)
                total_bytes_sent += len(chunk)
                print(f"  > Sent chunk: {len(chunk)} bytes", end='\r')

        print(f"\n[ElevenLabs] Finished streaming. Total bytes sent: {total_bytes_sent}")

    except Exception as e:
        print(f"[ElevenLabs Error] Could not stream audio: {e}")


# --- Main function to process audio and orchestrate the conversation ---
def process_conversation(audio_bytes, conn):
    if not audio_bytes:
        print("[Server] No audio data received to process.")
        return

    duration = len(audio_bytes) / (SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH)
    print(f"\n[Gemini] Processing {len(audio_bytes)} bytes ({duration:.2f}s) of audio...")

    try:
        wav_data = create_wav_header(audio_bytes, SAMPLE_RATE, CHANNELS, SAMPLE_WIDTH)
        audio_part = {"mime_type": "audio/wav", "data": wav_data}

        prompt_parts = [
            "You are a helpful assistant. Provide a concise answer to the following audio.",
            audio_part
        ]

        response = genai.GenerativeModel(GEMINI_MODEL_NAME).generate_content(prompt_parts)
        gemini_response_text = response.text

        print("\n--- Gemini Response ---")
        print(gemini_response_text)
        print("-----------------------\n")

        # Now, send this text to ElevenLabs and stream the audio back
        generate_and_stream_elevenlabs_audio(gemini_response_text, conn)

    except Exception as e:
        print(f"[API Error] An error occurred: {e}")


# ==============================================================================
# --- MAIN SERVER LOGIC (REVISED FOR RELIABILITY) ---
# ==============================================================================
def main():
    local_ip = get_local_ip()
    print("--- ESP32 Audio Streaming Server ---")
    print(f"🚨 [Server] Use this IP in your ESP32 code: \"{local_ip}\"")
    print(f"[Server] Listening on {HOST}:{PORT}...")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((HOST, PORT))
        s.listen(1)

        while True:
            print("\n[Server] Waiting for a new ESP32 connection for a single conversation...")
            conn, addr = s.accept()
            # Use a 'with' statement to ensure the connection is closed automatically
            with conn:
                print(f"[Server] Connected by {addr}. Receiving audio...")
                audio_buffer = io.BytesIO()

                # Loop to receive all audio data for one recording
                while True:
                    try:
                        data = conn.recv(CHUNK_SIZE)
                        if not data:
                            break  # Connection closed prematurely

                        # Check for the end signal
                        if END_RECORDING_SIGNAL in data:
                            audio_part_before_signal = data.split(END_RECORDING_SIGNAL)[0]
                            audio_buffer.write(audio_part_before_signal)
                            print("\n[Server] End of recording signal received.")
                            break  # Exit the receive loop
                        else:
                            audio_buffer.write(data)
                            print(f"  > Receiving audio... Buffer size: {audio_buffer.tell()} bytes", end='\r')

                    except socket.error as e:
                        print(f"\n[Socket Error] During receive: {e}")
                        break

                # Process the conversation and stream the response back
                process_conversation(audio_buffer.getvalue(), conn)

            # The 'with conn' block is now finished.
            # Python automatically closes the connection here.
            print(f"[Server] Response sent. Connection with {addr} closed. Ready for next.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[Server] Shutting down.")
